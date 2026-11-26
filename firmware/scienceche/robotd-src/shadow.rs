//! Shadow mode: run a candidate policy beside the driving one and record what it would do.
//!
//! A candidate standing policy is loaded and inferred on the same observation the driving
//! policy saw, and its action is written to a report. It is never commanded. The point is to
//! let a new policy see this robot's real states — the falls, the odd poses — before it is
//! given the robot.
//!
//! # What a shadow result does and does not mean
//!
//! The observation on every tick is produced by what the *driving* policy did. A shadow
//! action is therefore "what I would do from here", never "what happens if I take over": a
//! get-up is a sequence whose later states only exist if the earlier actions were taken. The
//! report answers *is the candidate sane on this robot's states* — finite, in range, fast
//! enough. It does not measure whether the candidate would succeed at anything.
//!
//! # The one rule: the control thread never waits for this
//!
//! The control thread hands over an observation with [`ShadowHandle::submit`], which copies
//! one `Frame` into a bounded queue and returns whether it was taken. A full queue drops the
//! frame and counts it. The counters are atomics the worker never holds a lock across, so
//! there is nothing for the control thread to contend on, and no path from here to an actuator.
//!
//! The difference from the driving policy's action is recorded as a norm and nothing else.
//! A candidate that gets up where the old policy is stuck *should* differ, so a large norm is
//! not evidence of anything wrong and gates nothing.
//!
//! # Stopping does not depend on the queue
//!
//! The handle is cloned into the control thread, so closing the queue is not this module's
//! to do. The worker is told to stop through a flag and waits on the queue with a timeout,
//! so `stop` returns whether or not any other handle is still alive.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{RecvTimeoutError, Receiver, SyncSender, TrySendError, sync_channel};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use duck_control::obs::{ACTION_LEN, Observation};
use duck_control::policy::ShadowPolicy;

/// Inferences per second: one in five control ticks.
pub const RATE_HZ: u32 = 10;

/// How many frames may wait. A burst fills this and the rest are dropped: the newest frame is
/// the useful one, and a backlog of stale observations is worth nothing.
pub const QUEUE_CAPACITY: usize = 4;

/// An action component at or beyond this is saturated — the candidate is asking for a joint
/// offset so large it is not a pose. Recorded; the inference itself succeeded.
pub const SATURATION: f32 = 10.0;

/// How the worker waits between frames. Only an upper bound on how long `stop` takes.
const WAKE: Duration = Duration::from_millis(50);

/// Nice value for the worker thread. The control thread stays at its default; this only ever
/// lowers the shadow's claim on the CPU relative to it.
const NICE: libc::c_int = 10;

/// One observation handed from the control thread, with what the driving policy did with it.
#[derive(Debug, Clone, Copy)]
pub struct Frame {
    /// The observation the driving policy acted on, as it was. `Observation` is fixed-width
    /// by type, so handing it over is a copy — not a conversion, and so not something that
    /// can fail on the control thread.
    pub obs: Observation,
    /// The driving policy's raw output on this tick, when it had one (`held` ticks do not).
    pub main_action: Option<[f32; ACTION_LEN]>,
    /// The control thread's own fall verdict on this tick.
    pub fallen: bool,
}

/// Counters. All atomics: the report is read from the IPC thread while the worker runs, and
/// the control thread writes `submitted` and `dropped` on its own thread.
#[derive(Debug, Default)]
pub struct Stats {
    /// Frames the control thread offered.
    pub submitted: AtomicU64,
    /// Frames not inferred because the queue was full when offered.
    pub dropped_full: AtomicU64,
    /// Frames not inferred because one newer was already waiting: the worker takes the
    /// newest and skips what it superseded. Counted apart from `dropped_full` — that one is
    /// the shadow falling behind, this one is the control thread offering faster than 10 Hz,
    /// which is by design.
    pub skipped_superseded: AtomicU64,
    /// Inferences completed, whether or not the output was usable.
    pub inferred: AtomicU64,
    /// Inferences that failed, or returned something other than 14 finite floats.
    pub invalid: AtomicU64,
    /// Inferences with a component at or beyond [`SATURATION`].
    pub saturated: AtomicU64,
    /// Completed inferences taken while the robot was down.
    pub fallen_inferred: AtomicU64,
    /// Rising edges of `fallen` across inferred frames: one per fall.
    pub falls: AtomicU64,
    /// Total inference time in nanoseconds, for the mean.
    pub elapsed_ns_sum: AtomicU64,
    /// Longest single inference in nanoseconds.
    pub elapsed_ns_max: AtomicU64,
    /// Sum of the difference norm, as `f64` bits, over frames that had a main action.
    diff_sum_bits: AtomicU64,
    /// Frames whose difference norm was recorded.
    pub diff_count: AtomicU64,
}

impl Stats {
    /// Sum of the recorded norms. Zero bits are a zero sum, so a fresh counter reads as 0.0.
    fn diff_sum(&self) -> f64 {
        f64::from_bits(self.diff_sum_bits.load(Ordering::Relaxed))
    }

    fn add_diff(&self, norm: f64) {
        let mut current = self.diff_sum_bits.load(Ordering::Relaxed);
        loop {
            let next = (f64::from_bits(current) + norm).to_bits();
            match self.diff_sum_bits.compare_exchange_weak(
                current,
                next,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => return,
                Err(actual) => current = actual,
            }
        }
    }
}

/// Anything that can be run against an observation. The real one is [`ShadowPolicy`]; tests
/// pass a fake so that queueing and failure handling are exercised without a model.
pub trait Infer {
    fn infer(&mut self, obs: &Observation) -> Result<[f32; ACTION_LEN], String>;
}

impl Infer for ShadowPolicy {
    fn infer(&mut self, obs: &Observation) -> Result<[f32; ACTION_LEN], String> {
        ShadowPolicy::infer(self, obs).map_err(|e| e.to_string())
    }
}

/// What the control thread holds. Clonable; `submit` touches no lock and allocates nothing.
#[derive(Clone)]
pub struct ShadowHandle {
    tx: Option<SyncSender<Frame>>,
    stats: Arc<Stats>,
    /// Cleared by [`ShadowHandle::disable`]. The control thread stops offering and the worker
    /// exits. Set again only by starting a new shadow.
    enabled: Arc<AtomicBool>,
    path: Option<PathBuf>,
    sha256: Option<String>,
}

impl ShadowHandle {
    /// A handle with no candidate behind it: every submit is a no-op that returns `false`.
    pub fn off() -> Self {
        Self {
            tx: None,
            stats: Arc::default(),
            enabled: Arc::new(AtomicBool::new(false)),
            path: None,
            sha256: None,
        }
    }

    pub fn is_active(&self) -> bool {
        self.tx.is_some() && self.enabled.load(Ordering::Relaxed)
    }

    /// The file being shadowed and its digest. The deployment gate matches on the digest so a
    /// report can never stand in for a different candidate.
    pub fn loaded(&self) -> Option<(&Path, &str)> {
        // `as_deref` on an `Option<PathBuf>` is already a `&Path`.
        self.sha256
            .as_deref()
            .and_then(|sha| self.path.as_deref().map(|path| (path, sha)))
    }

    /// Hand the control thread's observation to the shadow. Never waits, never allocates.
    ///
    /// Returns whether the frame was queued. With no candidate, or after `disable`, this is
    /// one relaxed load.
    pub fn submit(&self, frame: Frame) -> bool {
        let Some(tx) = &self.tx else { return false };
        if !self.enabled.load(Ordering::Relaxed) {
            return false;
        }
        self.stats.submitted.fetch_add(1, Ordering::Relaxed);
        match tx.try_send(frame) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) => {
                self.stats.dropped_full.fetch_add(1, Ordering::Relaxed);
                false
            }
            Err(TrySendError::Disconnected(_)) => {
                // The receiver is gone. Stop offering rather than counting a drop every tick.
                self.enabled.store(false, Ordering::Relaxed);
                false
            }
        }
    }

    /// Stop the shadow. Takes effect on the control thread's next tick; the worker exits
    /// within [`WAKE`] of its own accord, whether or not any other handle clone is alive.
    pub fn disable(&self) {
        self.enabled.store(false, Ordering::Relaxed);
    }

    /// The live counters. For tests watching a run as it happens; the wire has `report`.
    #[cfg(test)]
    pub fn stats(&self) -> &Stats {
        &self.stats
    }
}

/// The counters as the report and the gate read them. A point-in-time copy.
#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub path: Option<PathBuf>,
    pub sha256: Option<String>,
    pub rate_hz: u32,
    pub active: bool,
    /// Seconds the worker has run with a candidate. Kept after `stop`, unlike `active`.
    pub running_secs: f64,
    pub submitted: u64,
    pub dropped_full: u64,
    pub skipped_superseded: u64,
    pub inferred: u64,
    pub invalid: u64,
    pub saturated: u64,
    pub fallen_inferred: u64,
    pub falls: u64,
    /// Mean and worst inference time in milliseconds.
    pub elapsed_ms_mean: f64,
    pub elapsed_ms_max: f64,
    /// Mean of the difference norm over frames that had a main action. Informational only.
    pub diff_norm_mean: f64,
    pub diff_count: u64,
}

impl Report {
    /// Share of inferences that were invalid; `None` before the first inference.
    pub fn invalid_rate(&self) -> Option<f64> {
        (self.inferred > 0).then(|| self.invalid as f64 / self.inferred as f64)
    }
}

/// The wire form of a [`Report`]. Field for field, so that a field added to one side and not
/// the other is a compile error here rather than a figure that quietly goes missing.
impl From<&Report> for duck_ipc_proto::ShadowReportResult {
    fn from(r: &Report) -> Self {
        Self {
            path: r.path.as_ref().map(|p| p.display().to_string()),
            sha256: r.sha256.clone(),
            rate_hz: r.rate_hz,
            active: r.active,
            running_secs: r.running_secs,
            submitted: r.submitted,
            dropped_full: r.dropped_full,
            skipped_superseded: r.skipped_superseded,
            inferred: r.inferred,
            invalid: r.invalid,
            saturated: r.saturated,
            fallen_inferred: r.fallen_inferred,
            falls: r.falls,
            elapsed_ms_mean: r.elapsed_ms_mean,
            elapsed_ms_max: r.elapsed_ms_max,
            diff_norm_mean: r.diff_norm_mean,
            diff_count: r.diff_count,
        }
    }
}

/// Lower this thread's scheduling priority relative to the rest of the process.
///
/// Best effort: on a system that refuses it the shadow still runs, only not at a lower
/// priority, and that is said once rather than treated as a reason not to shadow at all.
fn lower_own_priority() -> Result<(), String> {
    // SAFETY: `setpriority` is a plain syscall taking and returning integers.
    //
    // `PRIO_PROCESS` with `who` = 0 is the calling thread. On Linux the nice value is a
    // per-thread attribute and the kernel takes 0 to mean whoever is calling, so this
    // lowers the shadow thread and nothing else — not the control thread, not the process.
    //
    // No `gettid()` here, on purpose. It is a glibc 2.30 wrapper, and the libc this daemon
    // links against is older than that: `std` gets away with naming it because its
    // reference is weak, where a strong one — which this was — fails the link.
    unsafe {
        if libc::setpriority(libc::PRIO_PROCESS, 0, NICE) != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
    }
    Ok(())
}

fn worker<I: Infer + Send + 'static>(rx: Receiver<Frame>, mut candidate: I, shared: Arc<Shared>) {
    if let Err(reason) = lower_own_priority() {
        tracing::info!(%reason, "shadow policy thread could not lower its priority");
    }
    let stats = &shared.stats;
    let period = Duration::from_micros(1_000_000 / u64::from(RATE_HZ.max(1)));
    let mut next = Instant::now();
    let mut was_fallen = false;

    // `recv_timeout` rather than `recv`: the stop flag is checked on every wake, so the
    // worker leaves on its own even while a cloned sender keeps the queue open.
    //
    // And a timeout is not an exit. The control thread offers a frame only on a tick where the
    // driving policy produced a step, so there are gaps longer than `WAKE` between frames — the
    // homing ramp before the first one, to start with. As `while let Ok(recv_timeout(..))` the
    // worker took its first quiet `WAKE` for its last: a shadow that loaded, ran for 50 ms and
    // stopped before it had seen anything. Every other test here feeds frames without a pause,
    // which is why none of them noticed; the one that runs the real control loop did.
    while shared.enabled.load(Ordering::Relaxed) {
        let mut frame = match rx.recv_timeout(WAKE) {
            Ok(frame) => frame,
            Err(RecvTimeoutError::Timeout) => continue,
            Err(RecvTimeoutError::Disconnected) => break,
        };
        // Act on the present, not the recent past: keep only the newest frame offered.
        while let Ok(newer) = rx.try_recv() {
            stats.skipped_superseded.fetch_add(1, Ordering::Relaxed);
            frame = newer;
        }
        let now = Instant::now();
        if now < next {
            // Not due yet. The frame is spent either way — holding it to run later would be
            // inferring on a state the robot has moved on from.
            stats.skipped_superseded.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        next = now + period;

        let outcome = {
            let t0 = Instant::now();
            let result = candidate.infer(&frame.obs);
            let elapsed = t0.elapsed().as_nanos();
            (result, elapsed.min(u128::from(u64::MAX)) as u64)
        };
        stats.inferred.fetch_add(1, Ordering::Relaxed);
        stats.elapsed_ns_sum.fetch_add(outcome.1, Ordering::Relaxed);
        stats.elapsed_ns_max.fetch_max(outcome.1, Ordering::Relaxed);

        if frame.fallen {
            stats.fallen_inferred.fetch_add(1, Ordering::Relaxed);
            if !was_fallen {
                stats.falls.fetch_add(1, Ordering::Relaxed);
            }
        }
        was_fallen = frame.fallen;

        match outcome.0 {
            Ok(action) if action.iter().all(|v| v.is_finite()) => {
                if action.iter().any(|v| v.abs() >= SATURATION) {
                    stats.saturated.fetch_add(1, Ordering::Relaxed);
                }
                if let Some(main) = frame.main_action {
                    let norm = action
                        .iter()
                        .zip(main.iter())
                        .map(|(a, b)| f64::from(*a - *b).powi(2))
                        .sum::<f64>()
                        .sqrt();
                    stats.add_diff(norm);
                    stats.diff_count.fetch_add(1, Ordering::Relaxed);
                }
            }
            Ok(_) => {
                stats.invalid.fetch_add(1, Ordering::Relaxed);
                // A candidate that is broken is broken on every frame; one line per inference
                // would be a journal of nothing else. The counter carries the count.
                tracing::debug!("shadow policy returned a non-finite action");
            }
            Err(reason) => {
                stats.invalid.fetch_add(1, Ordering::Relaxed);
                tracing::debug!(%reason, "shadow policy inference failed");
            }
        }
    }
    shared.running_secs.store(
        shared.started.elapsed().as_secs_f64().to_bits(),
        Ordering::Relaxed,
    );
}

/// What the worker and every handle share.
///
/// One instance per shadow, and the only thing they communicate through. The handle does
/// **not** keep counters or a flag of its own: if it did, the control thread would count on
/// one atomics set and the report would read another, and `disable` would clear a flag
/// nobody looks at.
struct Shared {
    stats: Arc<Stats>,
    /// An `Arc` so the handle holds the very flag the worker reads. If this were a bare
    /// `AtomicBool` the handle would need one of its own, and `disable` would stop the
    /// control thread offering without ever telling the worker to leave.
    enabled: Arc<AtomicBool>,
    /// When the worker started, fixed here so the report and the worker agree.
    started: Instant,
    /// `f64` bits: seconds run, written once when the worker exits, so the figure outlives
    /// the worker. Zero bits are 0.0 on a fresh instance.
    running_secs: AtomicU64,
}

impl Default for Shared {
    fn default() -> Self {
        Self {
            stats: Arc::default(),
            enabled: Arc::new(AtomicBool::new(false)),
            started: Instant::now(),
            running_secs: AtomicU64::new(0),
        }
    }
}

/// The running shadow: the handle for the control thread and the worker's thread.
pub struct Shadow {
    handle: ShadowHandle,
    worker: Option<JoinHandle<()>>,
    shared: Arc<Shared>,
    rate_hz: u32,
}

impl Shadow {
    /// Start shadowing `candidate`, which must already be loaded and warmed.
    pub fn start<I: Infer + Send + 'static>(
        candidate: I,
        path: Option<PathBuf>,
        sha256: Option<String>,
    ) -> Self {
        let (tx, rx) = sync_channel::<Frame>(QUEUE_CAPACITY);
        let shared = Arc::new(Shared::default());
        // Cloned out of `shared` *before* the worker takes its own clone, so the handle,
        // the worker and `stop` all hold the same flag and the same counters.
        let enabled = Arc::clone(&shared.enabled);
        let stats = Arc::clone(&shared.stats);
        shared.enabled.store(true, Ordering::Relaxed);
        let worker = {
            let shared = Arc::clone(&shared);
            std::thread::Builder::new()
                .name("shadow-policy".into())
                .spawn(move || worker(rx, candidate, shared))
                .ok()
        };
        let handle = ShadowHandle {
            tx: Some(tx),
            stats,
            enabled,
            path,
            sha256,
        };
        Self { handle, worker, shared, rate_hz: RATE_HZ }
    }

    /// Stop and wait for the worker. Idempotent, and returns even if the worker never
    /// started.
    pub fn stop(&mut self) {
        // One flag, shared: this tells the control thread to stop offering and the worker
        // to leave, and the handle's own `disable` is this same store.
        self.shared.enabled.store(false, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            // The worker wakes at least every `WAKE` to look at the flag, so this join is
            // bounded by that plus one inference.
            let _ = worker.join();
        }
    }

    pub fn handle(&self) -> ShadowHandle {
        self.handle.clone()
    }

    /// The live counters. For tests watching a run as it happens; the wire has `report`.
    #[cfg(test)]
    pub fn stats(&self) -> &Stats {
        &self.shared.stats
    }

    pub fn report(&self) -> Report {
        let s = &self.shared.stats;
        let inferred = s.inferred.load(Ordering::Relaxed);
        let sum_ns = s.elapsed_ns_sum.load(Ordering::Relaxed);
        let diff_count = s.diff_count.load(Ordering::Relaxed);
        let active = self.handle.is_active();
        Report {
            path: self.handle.path.clone(),
            sha256: self.handle.sha256.clone(),
            rate_hz: self.rate_hz,
            active,
            running_secs: if active {
                self.shared.started.elapsed().as_secs_f64()
            } else {
                // The worker wrote its final figure on the way out; before that it is 0.0.
                f64::from_bits(self.shared.running_secs.load(Ordering::Relaxed))
            },
            submitted: s.submitted.load(Ordering::Relaxed),
            dropped_full: s.dropped_full.load(Ordering::Relaxed),
            skipped_superseded: s.skipped_superseded.load(Ordering::Relaxed),
            inferred,
            invalid: s.invalid.load(Ordering::Relaxed),
            saturated: s.saturated.load(Ordering::Relaxed),
            fallen_inferred: s.fallen_inferred.load(Ordering::Relaxed),
            falls: s.falls.load(Ordering::Relaxed),
            elapsed_ms_mean: if inferred > 0 {
                sum_ns as f64 / 1e6 / inferred as f64
            } else {
                0.0
            },
            elapsed_ms_max: s.elapsed_ns_max.load(Ordering::Relaxed) as f64 / 1e6,
            diff_norm_mean: if diff_count > 0 {
                s.diff_sum() / diff_count as f64
            } else {
                0.0
            },
            diff_count,
        }
    }
}

/// Why a shadow did not start, in the words an operator needs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NotStarted {
    /// No candidate configured: `[policy] shadow_stand` unset or `"none"`. Not an error.
    NotConfigured,
    /// The candidate is the same file as the live standing policy.
    ShadowsItself(PathBuf),
    /// The file would not load: wrong shape, unreadable, runtime missing.
    WouldNotLoad(String),
}

impl std::fmt::Display for NotStarted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotConfigured => write!(f, "no shadow policy configured"),
            Self::ShadowsItself(path) => write!(
                f,
                "shadow is the live standing policy ({}): it would be compared with itself",
                path.display()
            ),
            Self::WouldNotLoad(reason) => write!(f, "shadow policy did not load: {reason}"),
        }
    }
}

/// Load the candidate named by the configuration, if there is one.
///
/// Separate from the controller's own load on purpose: a candidate that will not load must
/// cost its report and nothing else, never the walking policy, never the health verdict.
/// The caller logs the reason and goes on.
///
/// A candidate that *is* the live standing policy is refused. The whole point is to compare
/// a candidate against what runs today; pointed at today's file the shadow would agree with
/// itself on every frame, the difference would read zero, and a deployment gate would pass
/// on a report that measured nothing.
///
/// Checked twice. By path first, which is free and catches the plain mistake before a model
/// is loaded. Then by content, because a path is only a way of naming a file: the same file
/// can be written `…/abc/policy.onnx`, `…/abc/./policy.onnx` or `…/abc/../abc/policy.onnx`,
/// and each would have passed a string comparison. The digest does not care how it is spelled.
///
/// The live policy's digest is hashed from its file rather than asked of the loaded network,
/// so that this stays independent of the controller.
pub fn load_configured(
    candidate: Option<&Path>,
    live_stand: Option<&Path>,
) -> Result<Shadow, NotStarted> {
    load_configured_with(candidate, live_stand, |path| {
        duck_control::policy::file_sha256(path).ok()
    })
}

/// Whether a candidate may be shadowed, before anything is loaded.
///
/// `Err(NotConfigured)` when there is nothing to shadow, which is not a failure.
/// `Err(ShadowsItself)` when the candidate is the live policy by path.
///
/// A refusal is only ever an `Err`, never an `Ok(false)` the caller has to remember to
/// test: a bool that means "no" is one forgotten `if` away from a shadow comparing the live
/// policy with itself.
///
/// `Path` equality already sees through a `.` or a doubled component, so more spellings of
/// one file are caught here than a string comparison would find. It is still a comparison
/// of names — a symlink, or a copy, names the same bytes differently — which is what the
/// digest check after loading is for.
pub fn admissible_before_load<'a>(
    candidate: Option<&'a Path>,
    live_stand: Option<&Path>,
) -> Result<&'a Path, NotStarted> {
    let candidate = candidate.ok_or(NotStarted::NotConfigured)?;
    if live_stand.is_some_and(|live| live == candidate) {
        return Err(NotStarted::ShadowsItself(candidate.to_path_buf()));
    }
    Ok(candidate)
}

/// Whether a loaded candidate may be shadowed, given its digest and the live policy's.
///
/// Equal digests mean the same bytes: the candidate is the live policy under another name.
pub fn admissible_after_load(candidate_sha256: &str, live_sha256: Option<&str>) -> bool {
    live_sha256.is_none_or(|live| live != candidate_sha256)
}

/// [`load_configured`], with the live file's digest supplied by the caller, so that the
/// content check is not tied to where the digest comes from.
fn load_configured_with(
    candidate: Option<&Path>,
    live_stand: Option<&Path>,
    live_digest: impl Fn(&Path) -> Option<String>,
) -> Result<Shadow, NotStarted> {
    // Hands back the candidate unwrapped: having passed the check and being `Some` are
    // settled together here, so nothing below has to unwrap or assume.
    let candidate = admissible_before_load(candidate, live_stand)?;
    let policy = ShadowPolicy::load(candidate)
        .map_err(|e| NotStarted::WouldNotLoad(e.to_string()))?;
    let sha256 = policy.loaded_model().sha256.clone();
    let live_sha256 = live_stand.and_then(&live_digest);
    if !admissible_after_load(&sha256, live_sha256.as_deref()) {
        return Err(NotStarted::ShadowsItself(candidate.to_path_buf()));
    }
    Ok(Shadow::start(policy, Some(candidate.to_path_buf()), Some(sha256)))
}

impl Drop for Shadow {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use duck_control::obs::OBS_LEN;
    use std::sync::atomic::AtomicUsize;

    /// A stand-in candidate: counts its calls, and returns a fixed action or an error.
    struct Fake {
        calls: Arc<AtomicUsize>,
        result: Result<[f32; ACTION_LEN], String>,
    }

    impl Infer for Fake {
        fn infer(&mut self, _obs: &Observation) -> Result<[f32; ACTION_LEN], String> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            self.result.clone()
        }
    }

    fn start_with(result: Result<[f32; ACTION_LEN], String>) -> (Shadow, Arc<AtomicUsize>) {
        let calls = Arc::new(AtomicUsize::new(0));
        let fake = Fake { calls: Arc::clone(&calls), result };
        let path = Some(PathBuf::from("/models/candidate/policy.onnx"));
        (Shadow::start(fake, path, Some("abc123".into())), calls)
    }

    fn ok(action: [f32; ACTION_LEN]) -> Result<[f32; ACTION_LEN], String> {
        Ok(action)
    }

    fn frame(fallen: bool) -> Frame {
        Frame {
            obs: Observation::from([0.01; OBS_LEN]),
            main_action: Some([0.0; ACTION_LEN]),
            fallen,
        }
    }

    /// Feed frames until the worker has completed `n` inferences, or give up.
    ///
    /// `fallen` is a function of *time since feeding began*, in seconds, not of the frame
    /// count. The worker infers at 10 Hz whatever rate frames arrive at, so a state defined
    /// per frame would be sampled at seemingly arbitrary points: frames go in 200 to the
    /// second and one in eighteen is used. Defined per second, the state is what the test
    /// says it is however the two rates compare.
    fn feed_until(shadow: &Shadow, n: u64, fallen: impl Fn(f64) -> bool) -> bool {
        let start = Instant::now();
        let deadline = start + Duration::from_secs(20);
        while Instant::now() < deadline {
            if shadow.stats().inferred.load(Ordering::Relaxed) >= n {
                return true;
            }
            shadow.handle().submit(frame(fallen(start.elapsed().as_secs_f64())));
            std::thread::sleep(Duration::from_millis(5));
        }
        false
    }

    /// Poll until `f` holds, up to five seconds.
    fn eventually(f: impl Fn() -> bool) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if f() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        f()
    }

    /// **Off means off.** The control thread calls `submit` on every tick the driving policy
    /// answers, whether or not a shadow exists. With none configured that call must cost
    /// next to nothing: this is the "no effect on the main loop when off" guarantee, measured
    /// rather than asserted.
    ///
    /// The bound is generous on purpose — 200 ns a call, against a 20 ms control period — so
    /// that a slow or loaded test machine cannot fail it, while anything that actually did
    /// work (a lock, a syscall, an allocation) would still be caught by orders of magnitude.
    #[test]
    fn submit_with_no_shadow_costs_nothing() {
        let h = ShadowHandle::off();
        let f = frame(false);
        // Warm: the first call may fault the code page in.
        for _ in 0..1000 {
            h.submit(f);
        }
        let n = 50_000u32;
        let t0 = Instant::now();
        for _ in 0..n {
            h.submit(f);
        }
        let took = t0.elapsed();
        let per_call = took.as_nanos() / u128::from(n);
        assert!(
            per_call < 200,
            "submit with no shadow took {per_call} ns a call over {n} calls ({took:?})"
        );
        // And it did nothing: nothing queued, nothing counted.
        assert!(!h.is_active());
        assert_eq!(h.stats().submitted.load(Ordering::Relaxed), 0);
    }

    /// After `disable` the same: the handle is still in the control thread, still called.
    #[test]
    fn submit_after_disable_costs_nothing_and_does_nothing() {
        let (mut shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        let h = shadow.handle();
        h.disable();
        shadow.stop();
        let before = shadow.report();
        let f = frame(false);
        let t0 = Instant::now();
        for _ in 0..50_000 {
            h.submit(f);
        }
        let per_call = t0.elapsed().as_nanos() / 50_000;
        assert!(per_call < 200, "submit after disable took {per_call} ns a call");
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(calls.load(Ordering::SeqCst), before.inferred as usize);
        assert_eq!(shadow.report().submitted, before.submitted, "nothing was offered");
    }

    /// The handle with no candidate accepts anything and does nothing.
    #[test]
    fn off_handle_is_inert() {
        let h = ShadowHandle::off();
        assert!(!h.is_active());
        assert!(!h.submit(frame(false)));
        assert_eq!(h.stats().submitted.load(Ordering::Relaxed), 0);
        assert_eq!(h.loaded(), None);
    }

    /// **The property the control thread depends on.** With nothing draining the queue, a
    /// caller offering far more frames than it holds must come back promptly every time.
    #[test]
    fn submit_never_waits_for_a_full_queue() {
        let (shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        // Stop the worker without dropping the sender, so nothing ever drains the queue.
        shadow.shared.enabled.store(false, Ordering::Relaxed);
        // The worker is parked in `recv_timeout`: it wakes within `WAKE`, reads the flag and
        // leaves. Wait until it actually has. It has to be waited on and not assumed, because
        // the flag it reads is the very flag the handle is re-enabled through below — a worker
        // still parked would wake to find it set again and carry on. (This used to wait on the
        // flag, which was already clear, so it waited for nothing; the worker was in fact
        // leaving on its first receive timeout, which is not what a timeout is for.)
        let deadline = Instant::now() + Duration::from_secs(5);
        while !shadow.worker.as_ref().is_some_and(|w| w.is_finished()) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(
            shadow.worker.as_ref().is_some_and(|w| w.is_finished()),
            "the worker did not leave on the stop flag"
        );
        let h = shadow.handle();
        // Re-enable offering: the sender is still open, the receiver is being dropped.
        h.enabled.store(true, Ordering::Relaxed);
        std::thread::sleep(Duration::from_millis(200)); // let the worker's receiver drop
        let t0 = Instant::now();
        for _ in 0..100_000 {
            h.submit(frame(false));
        }
        let took = t0.elapsed();
        assert!(
            took < Duration::from_secs(2),
            "100k submits against a dead or full queue took {took:?}"
        );
        assert_eq!(calls.load(Ordering::SeqCst), 0, "nothing was ever inferred");
    }

    /// **The shadow falling behind.** With the worker stuck inside one inference, frames
    /// keep arriving: the queue must hold no more than it was built to, the rest must be
    /// counted as dropped rather than queued, and the control thread must not have waited.
    #[test]
    fn a_full_queue_drops_and_counts() {
        // A candidate that takes a long time keeps the worker busy, so the queue fills.
        struct Slow(Arc<AtomicUsize>);
        impl Infer for Slow {
            fn infer(&mut self, _obs: &Observation) -> Result<[f32; ACTION_LEN], String> {
                self.0.fetch_add(1, Ordering::SeqCst);
                std::thread::sleep(Duration::from_millis(400));
                Ok([0.0; ACTION_LEN])
            }
        }
        let calls = Arc::new(AtomicUsize::new(0));
        let mut shadow = Shadow::start(Slow(Arc::clone(&calls)), None, None);
        let h = shadow.handle();
        assert!(h.is_active(), "the shadow must be live while its worker infers");

        // Keep offering frames until the worker is *inside* the inference. It infers nothing
        // until a frame arrives, so waiting for it without offering any is two threads
        // waiting on each other — which is what an earlier version of this test did, timed
        // out after five seconds, and then went on as though the worker were busy.
        let entered = eventually(|| {
            h.submit(frame(false));
            calls.load(Ordering::SeqCst) >= 1
        });
        assert!(entered, "the worker never started: {:?}", shadow.report());
        // The count is raised on entry, so from here the worker cannot drain for 400 ms.

        let before = (
            h.stats().submitted.load(Ordering::Relaxed),
            h.stats().dropped_full.load(Ordering::Relaxed),
        );
        let t0 = Instant::now();
        let mut queued = 0usize;
        for _ in 0..QUEUE_CAPACITY + 50 {
            if h.submit(frame(false)) {
                queued += 1;
            }
        }
        let took = t0.elapsed();
        let offered = QUEUE_CAPACITY + 50;

        assert!(h.is_active(), "still live: nothing here should stop the shadow");
        assert!(
            took < Duration::from_millis(500),
            "offering {offered} frames to a busy shadow took {took:?}"
        );
        assert_eq!(
            h.stats().submitted.load(Ordering::Relaxed) - before.0,
            offered as u64,
            "every frame was offered"
        );
        // The queue may hold fewer than its capacity if the worker took one on its way in;
        // it may never hold more.
        assert!(
            queued <= QUEUE_CAPACITY,
            "at most {QUEUE_CAPACITY} frames queued, got {queued}"
        );
        let dropped = h.stats().dropped_full.load(Ordering::Relaxed) - before.1;
        assert_eq!(
            dropped,
            (offered - queued) as u64,
            "every frame not queued is counted as dropped"
        );
        assert!(dropped > 0, "the queue was actually full at some point");
        shadow.stop();
    }

    /// The rate is about RATE_HZ, not the rate the control thread offers at.
    #[test]
    fn infers_at_about_the_configured_rate() {
        let (mut shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        // Offer as fast as possible for a fixed time; count what the worker did.
        let h = shadow.handle();
        let deadline = Instant::now() + Duration::from_millis(3000);
        while Instant::now() < deadline {
            h.submit(frame(false));
            std::thread::yield_now();
        }
        shadow.stop();
        let n = calls.load(Ordering::SeqCst) as f64;
        // 3 s at 10 Hz is 30. Timers on a loaded box drift; accept 15..60 so the test fails
        // on "ran at 50 Hz" and on "barely ran", not on scheduling noise.
        assert!((15.0..=60.0).contains(&n), "inferred {n} times in 3 s, expected about 30");
    }

    /// An inert handle and a disabled one look the same to the control thread.
    #[test]
    fn a_disabled_handle_stops_offering() {
        let (mut shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        let h = shadow.handle();
        assert!(h.is_active());
        h.disable();
        assert!(!h.is_active());
        assert!(!h.submit(frame(false)));
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(calls.load(Ordering::SeqCst), 0, "nothing reached the candidate");
        shadow.stop();
    }

    /// A candidate that fails every time is counted, and the worker keeps asking it.
    #[test]
    fn a_failing_candidate_is_counted_not_fatal() {
        let (mut shadow, calls) = start_with(Err("boom".into()));
        assert!(feed_until(&shadow, 20, |_| false), "the worker kept going");
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 20, "{r:?}");
        assert_eq!(r.invalid, r.inferred, "every inference failed: {r:?}");
        assert_eq!(r.invalid_rate(), Some(1.0));
        assert!(calls.load(Ordering::SeqCst) >= 20);
    }

    /// A NaN action is invalid although the inference itself "succeeded".
    #[test]
    fn a_non_finite_action_is_invalid() {
        let mut action = [0.5; ACTION_LEN];
        action[3] = f32::NAN;
        let (mut shadow, _) = start_with(ok(action));
        assert!(feed_until(&shadow, 10, |_| false));
        shadow.stop();
        let r = shadow.report();
        assert_eq!(r.invalid, r.inferred, "{r:?}");
        assert_eq!(r.saturated, 0, "a NaN is not a saturation: {r:?}");
        assert_eq!(r.diff_count, 0, "no norm is recorded for an invalid action: {r:?}");
    }

    /// An infinite action is invalid, the same as a NaN.
    #[test]
    fn an_infinite_action_is_invalid() {
        let mut action = [0.5; ACTION_LEN];
        action[7] = f32::INFINITY;
        let (mut shadow, _) = start_with(ok(action));
        assert!(feed_until(&shadow, 10, |_| false));
        shadow.stop();
        assert_eq!(shadow.report().invalid, shadow.report().inferred);
    }

    /// A large but finite action is saturated, not invalid.
    #[test]
    fn a_saturated_action_is_recorded_as_such() {
        let mut action = [0.5; ACTION_LEN];
        action[0] = SATURATION;
        let (mut shadow, _) = start_with(ok(action));
        assert!(feed_until(&shadow, 10, |_| false));
        shadow.stop();
        let r = shadow.report();
        assert_eq!(r.saturated, r.inferred, "{r:?}");
        assert_eq!(r.invalid, 0, "saturated is not invalid: {r:?}");
    }

    /// **A large difference is not a failure.** A candidate that moves where the driving
    /// policy holds still is what shadow mode exists to look at; the norm is recorded and
    /// nothing in the report calls it invalid.
    #[test]
    fn a_large_difference_is_recorded_and_is_not_a_verdict() {
        let action = [3.0; ACTION_LEN]; // well clear of the driving policy's zeros
        let (mut shadow, _) = start_with(ok(action));
        assert!(feed_until(&shadow, 10, |_| false));
        shadow.stop();
        let r = shadow.report();
        assert_eq!(r.invalid, 0, "{r:?}");
        assert_eq!(r.saturated, 0, "3.0 is a large command, not saturation: {r:?}");
        assert_eq!(r.diff_count, r.inferred, "{r:?}");
        let want = (3.0f64).powi(2).mul_add(ACTION_LEN as f64, 0.0).sqrt();
        assert!((r.diff_norm_mean - want).abs() < 1e-3, "{} vs {want}", r.diff_norm_mean);
    }

    /// No main action on the frame (`held` ticks): the inference counts, the norm does not.
    #[test]
    fn without_a_main_action_no_norm_is_recorded() {
        let (mut shadow, _) = start_with(ok([1.0; ACTION_LEN]));
        let h = shadow.handle();
        let deadline = Instant::now() + Duration::from_secs(5);
        while shadow.stats().inferred.load(Ordering::Relaxed) < 10 && Instant::now() < deadline {
            h.submit(Frame { main_action: None, ..frame(false) });
            std::thread::sleep(Duration::from_millis(20));
        }
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 10, "{r:?}");
        assert_eq!(r.diff_count, 0, "{r:?}");
        assert_eq!(r.diff_norm_mean, 0.0);
    }

    /// One fall per rising edge, however long each lasts and however many there are.
    ///
    /// The input is two seconds of half-second halves: down, up, down, up. At 10 Hz that is
    /// about twenty inferred frames and exactly two rising edges.
    #[test]
    fn falls_are_counted_on_rising_edges() {
        let (mut shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        assert!(feed_until(&shadow, 20, |t| (t * 2.0) as u64 % 2 == 0));
        shadow.stop();
        let r = shadow.report();
        assert_eq!(r.falls, 2, "{r:?}");
        assert!(r.fallen_inferred > 0, "{r:?}");
        assert!(r.fallen_inferred < r.inferred, "{r:?}");
        // Every inferred frame should have been down or up, never something in between: the
        // halves are half a second and a frame every tenth of one, so about half are down.
        let ratio = r.fallen_inferred as f64 / r.inferred as f64;
        assert!((0.25..=0.75).contains(&ratio), "fallen share {ratio}: {r:?}");
    }

    /// The report names the candidate it measured.
    #[test]
    fn the_report_identifies_the_candidate() {
        let (shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        let r = shadow.report();
        assert_eq!(r.sha256.as_deref(), Some("abc123"));
        assert_eq!(
            r.path.as_deref(),
            Some(Path::new("/models/candidate/policy.onnx"))
        );
        assert_eq!(r.rate_hz, RATE_HZ);
        assert!(r.active);
        assert_eq!(r.invalid_rate(), None, "nothing inferred yet");
    }

    /// **Stop returns promptly, with the control thread's handle still alive.** The handle is
    /// cloned into the control thread, so stopping must not wait for every clone to drop.
    #[test]
    fn stop_returns_while_a_handle_clone_is_alive() {
        let (mut shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        let control_threads_copy = shadow.handle(); // deliberately kept alive
        assert!(control_threads_copy.is_active());
        let t0 = Instant::now();
        shadow.stop();
        let took = t0.elapsed();
        assert!(
            took < Duration::from_secs(2),
            "stop took {took:?} with a live handle clone"
        );
        assert!(!shadow.handle().is_active());
        assert!(!control_threads_copy.submit(frame(false)), "the old clone is inert too");
    }

    /// Stopping twice is harmless.
    #[test]
    fn stop_is_idempotent() {
        let (mut shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        assert!(feed_until(&shadow, 5, |_| false));
        shadow.stop();
        let after_first = shadow.report();
        shadow.stop();
        assert_eq!(shadow.report(), after_first, "a second stop changes nothing");
    }

    /// The report keeps the running time and its numbers after the shadow stops: the
    /// deployment gate reads it then.
    #[test]
    fn the_report_survives_stop() {
        let (mut shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        assert!(feed_until(&shadow, 10, |_| false));
        shadow.stop();
        let r = shadow.report();
        assert!(!r.active);
        assert!(r.inferred >= 10, "{r:?}");
        assert!(r.running_secs > 0.0, "running time is kept after stop: {r:?}");
        assert_eq!(r.sha256.as_deref(), Some("abc123"));
        // And it does not keep growing.
        std::thread::sleep(Duration::from_millis(120));
        assert_eq!(shadow.report(), r);
    }

    /// Dropping stops the worker.
    #[test]
    fn drop_stops_the_worker() {
        let calls;
        {
            let (shadow, c) = start_with(ok([0.0; ACTION_LEN]));
            calls = c;
            assert!(feed_until(&shadow, 5, |_| false));
        }
        let settled = calls.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(calls.load(Ordering::SeqCst), settled, "no inference after drop");
    }

    /// **A gap in the frames is not the end.** The control thread offers a frame only on a tick
    /// where the driving policy produced a step, so there are quiet stretches longer than
    /// [`WAKE`] — the homing ramp before the first one, to start with. The worker used to take
    /// its first quiet `WAKE` for its last: a shadow that loaded, ran for 50 ms and stopped
    /// before it had seen anything. Every other test here feeds frames without a pause, which is
    /// why none of them noticed.
    #[test]
    fn the_worker_outlives_a_gap_in_frames() {
        let (mut shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        let gap = WAKE * 8;
        std::thread::sleep(gap);
        assert!(shadow.handle().is_active(), "the worker left during the gap");
        assert!(
            feed_until(&shadow, 10, |_| false),
            "nothing was inferred after the gap"
        );
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 10, "{r:?}");
        assert!(calls.load(Ordering::SeqCst) >= 10);
        assert!(
            r.running_secs >= gap.as_secs_f64(),
            "the worker ran through the gap: {r:?}"
        );
    }

    /// The nice value of this process's thread named `name`, read from `/proc`.
    ///
    /// Linux only, like the daemon. `/proc/<pid>/task/<tid>/stat` is `pid (comm) state …`: cut
    /// at the last `)`, and `nice` is the 17th field after it.
    fn nice_by_name(name: &str) -> Option<i32> {
        std::fs::read_dir("/proc/self/task")
            .ok()?
            .flatten()
            .filter_map(|entry| std::fs::read_to_string(entry.path().join("stat")).ok())
            .find_map(|stat| {
                let (head, tail) = stat.rsplit_once(')')?;
                let comm = head.rsplit_once('(')?.1;
                if comm != name {
                    return None;
                }
                tail.split_whitespace().nth(16)?.parse().ok()
            })
    }

    /// **The shadow runs at a lower priority, and nothing else is lowered with it.** The nice
    /// value is a per-thread attribute on Linux and `who` = 0 is the calling thread, which is
    /// what the worker passes. Read back from `/proc`, on the shadow thread and on this one,
    /// rather than taken on trust: this is the whole of "an independent, low-priority thread".
    #[test]
    fn the_shadow_thread_runs_niced_and_nothing_else_does() {
        let mine = std::thread::current().name().unwrap_or("main").to_owned();
        let before = nice_by_name(&mine);
        let (shadow, _) = start_with(ok([0.0; ACTION_LEN]));
        // The worker lowers itself as its first act; give it a moment to get there.
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if nice_by_name("shadow-policy").is_some_and(|nice| nice >= NICE) {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "the shadow thread never ran at nice {NICE}: {:?}",
                nice_by_name("shadow-policy")
            );
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            nice_by_name(&mine),
            before,
            "lowering the shadow must not have lowered the thread that started it"
        );
        drop(shadow);
    }

    // ---- Which candidates may be shadowed ----
    //
    // These need no model: the two checks are pure, and the load sits between them.

    /// `&'static Path` from a string literal, so the paths below can be written inline.
    fn p(s: &'static str) -> &'static Path {
        Path::new(s)
    }

    fn as_live(s: &'static str) -> Option<&'static Path> {
        Some(p(s))
    }

    /// Nothing configured is not a failure, and must not read as one in the log.
    #[test]
    fn no_candidate_is_not_configured() {
        assert_eq!(
            admissible_before_load(None, as_live("/live/policy.onnx")),
            Err(NotStarted::NotConfigured)
        );
        assert_eq!(admissible_before_load(None, None), Err(NotStarted::NotConfigured));
    }

    /// The plain mistake: the live policy's path typed into the shadow key. Refused as an
    /// `Err`, so there is no bool for the caller to forget to test.
    #[test]
    fn the_live_policy_by_path_is_refused_before_loading() {
        let live = p("/models/abc/policy.onnx");
        assert_eq!(
            admissible_before_load(Some(live), Some(live)),
            Err(NotStarted::ShadowsItself(live.to_path_buf()))
        );
        assert_eq!(
            NotStarted::ShadowsItself(live.to_path_buf()).to_string(),
            "shadow is the live standing policy (/models/abc/policy.onnx): \
             it would be compared with itself"
        );
    }

    /// **`Path` equality is not string equality — and not file identity either.** Measured
    /// here, not assumed: it sees through a `.` and through a doubled separator, but it does
    /// *not* fold `..`. `/a/b/c/../policy.onnx` and `/a/b/policy.onnx` compare unequal while
    /// naming the same file.
    ///
    /// So the path check catches the two spellings below and lets the third through. That
    /// third is exactly why there is a second check by digest after loading: by then the
    /// question is whether the bytes are the same, which no spelling can talk its way out of.
    ///
    /// (An earlier version of this test asserted that *none* of the three would be caught,
    /// on the assumption that `==` compared strings. That was wrong in the other direction.
    /// Both versions failed against the real behaviour; this one records it.)
    #[test]
    fn path_equality_covers_dot_and_empty_components_but_not_parent() {
        let live_path = "/models/abc/policy.onnx";

        // Caught by name alone.
        for same in ["/models/abc/./policy.onnx", "/models/abc//policy.onnx"] {
            assert_eq!(
                admissible_before_load(Some(p(same)), as_live(live_path)),
                Err(NotStarted::ShadowsItself(PathBuf::from(same))),
                "{same}"
            );
        }
        // Not caught by name: `..` is left as written.
        let via_parent = "/models/abc/abc/../policy.onnx";
        assert_eq!(
            admissible_before_load(Some(p(via_parent)), as_live(live_path)),
            Ok(p(via_parent)),
            "`..` must get past the path check — if this starts failing, Path::eq began \
             folding parents and the comment above is out of date"
        );
    }

    /// The case the path check lets through is the one the digest check exists for.
    #[test]
    fn the_parent_spelling_is_stopped_by_content() {
        // Same bytes under a name the path check cannot equate: only the digest knows.
        assert!(!admissible_after_load("deadbeef", Some("deadbeef")));
    }

    /// What the path check does *not* catch, and leaves to the digest: a different name.
    #[test]
    fn a_different_name_passes_the_path_check() {
        for other in [
            "/models/xyz/policy.onnx",      // a different file
            "/models/abc/policy.copy.onnx", // could be a copy of the same bytes
        ] {
            assert_eq!(
                admissible_before_load(Some(p(other)), as_live("/models/abc/policy.onnx")),
                Ok(p(other)),
                "{other}"
            );
        }
    }

    /// **The check a spelling cannot get around.** Same bytes, whatever the path says.
    #[test]
    fn the_same_content_is_refused_after_loading() {
        assert!(!admissible_after_load("deadbeef", Some("deadbeef")));
    }

    /// Different content is a real candidate.
    #[test]
    fn different_content_is_admissible() {
        assert!(admissible_after_load("deadbeef", Some("cafebabe")));
        // Same length, one character apart: the comparison is exact.
        assert!(admissible_after_load("deadbeef", Some("deadbeee")));
        assert!(admissible_after_load("deadbeef", Some("deadbee")));
    }

    /// With no live standing policy there is nothing to be the same as.
    #[test]
    fn with_no_live_policy_anything_is_admissible() {
        assert!(admissible_after_load("deadbeef", None));
        // Before loading too: the candidate comes back unwrapped, ready to be loaded.
        assert_eq!(
            admissible_before_load(Some(p("/models/abc/policy.onnx")), None),
            Ok(p("/models/abc/policy.onnx"))
        );
    }

    /// No digest for the live policy admits the candidate.
    ///
    /// This arises two ways — no live standing slot, or a live file that could not be read —
    /// and the check sees only the `None`. Admitting is right for both: with no slot there is
    /// nothing to be the same as, and an unreadable live file is the live policy's problem,
    /// not a reason to leave nothing shadowing. That an unreadable file *becomes* a `None`
    /// is one line in `load_configured_with` that only a real file can exercise.
    #[test]
    fn a_missing_live_digest_admits_the_candidate() {
        assert!(admissible_after_load("deadbeef", None));
        assert!(admissible_after_load("", None), "even a candidate with no digest of its own");
    }
    // ---- The real candidate, through the real loader ----
    //
    // Everything above hands the worker a stand-in. These load an actual network the way `main`
    // does on startup, so the path a robot takes on its first tick with a shadow has been run
    // somewhere before then. They need ONNX Runtime like the rest of the suite's real-model
    // tests, and like those they fail rather than skip without it.

    /// A policy shipped with the repository. `stand` is the file the stand slot loads, which is
    /// what a candidate for that slot has to be loadable as.
    fn bundled(slot: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../policies")
            .join(slot)
            .join("policy.onnx")
    }

    /// Enough different observations to see two loaders agree more than once. Not robot states:
    /// the point is only that the inputs vary from one call to the next.
    fn varied_observations() -> Vec<Observation> {
        (0..8)
            .map(|k| {
                Observation::from(std::array::from_fn(|i| {
                    ((i * 7 + k * 13) % 23) as f32 / 11.0 - 1.0
                }))
            })
            .collect()
    }

    /// The loaded candidate is identified by the SHA-256 of its file, hashed straight from disk.
    /// The deployment gate matches that figure against a package, so it has to be the file's own
    /// digest and not one of anything derived from it.
    #[test]
    fn a_real_candidate_loads_and_is_identified_by_its_file_digest() {
        let path = bundled("stand");
        let mut candidate = ShadowPolicy::load(&path).unwrap();
        assert_eq!(
            candidate.loaded_model().sha256,
            duck_control::policy::file_sha256(&path).unwrap(),
            "the report's digest must be the SHA-256 of the file itself"
        );
        let action = candidate.infer(&Observation::zeroed()).unwrap();
        assert!(action.iter().all(|v| v.is_finite()), "{action:?}");
    }

    /// What the shadow records is what the stand slot would command. Both load the same file
    /// through the same `open`, so on the same observations they must answer identically — a
    /// sequence, not one frame, so that a recurrent candidate's state is seen to track too.
    #[test]
    fn a_real_candidate_infers_what_the_stand_slot_would() {
        use duck_control::policy::{Net, Policy, PolicyPaths};
        let path = bundled("stand");
        let mut slot = Policy::load(
            &PolicyPaths {
                walk: bundled("walk"),
                stand: Some(path.clone()),
                ..PolicyPaths::default()
            },
            0.05,
        )
        .unwrap();
        let mut candidate = ShadowPolicy::load(&path).unwrap();
        assert_eq!(
            slot.loaded_model(Net::Stand).unwrap().sha256,
            candidate.loaded_model().sha256
        );
        for (n, obs) in varied_observations().into_iter().enumerate() {
            let from_slot = slot.infer(&obs, Net::Stand).unwrap();
            let from_shadow = candidate.infer(&obs).unwrap();
            assert_eq!(from_slot, from_shadow, "observation {n}");
        }
    }

    /// End to end, the way `main` starts it: the configured path loads, the worker runs, and the
    /// report carries that file's digest and counts real inferences.
    #[test]
    fn load_configured_runs_a_real_candidate() {
        let path = bundled("stand");
        let digest = duck_control::policy::file_sha256(&path).unwrap();
        let mut shadow = load_configured(Some(&path), None).unwrap();
        assert_eq!(
            shadow.handle().loaded(),
            Some((path.as_path(), digest.as_str())),
            "the report must name the file that was configured"
        );
        assert!(
            feed_until(&shadow, 10, |_| false),
            "the real network was never run"
        );
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 10, "{r:?}");
        assert_eq!(r.invalid, 0, "the shipped stand policy returns 14 finite actions: {r:?}");
        assert_eq!(r.sha256.as_deref(), Some(digest.as_str()));
    }

    /// The content check on real files. A copy of the live policy under another name is the live
    /// policy, and is refused once its digest is known; a file with other bytes is admitted.
    #[test]
    fn a_copy_of_the_live_policy_is_refused_by_its_digest() {
        let live = bundled("stand");
        let temp = tempfile::tempdir().unwrap();
        let copy = temp.path().join("candidate.onnx");
        std::fs::copy(&live, &copy).unwrap();
        assert!(
            matches!(
                load_configured(Some(&copy), Some(&live)),
                Err(NotStarted::ShadowsItself(p)) if p == copy
            ),
            "a copy of the live policy must not be shadowed"
        );
        assert!(
            load_configured(Some(&bundled("sitstand")), Some(&live)).is_ok(),
            "a different file is a legitimate candidate"
        );
    }

    /// A configured path that is not a loadable policy is `WouldNotLoad`. Nothing is left
    /// running, and the reason travels with the refusal.
    #[test]
    fn a_real_path_that_is_not_a_policy_does_not_load() {
        let not_a_model = bundled("stand").with_file_name("manifest.json");
        for path in [not_a_model, Path::new("/no/such/shadow/policy.onnx").to_path_buf()] {
            let reason = match load_configured(Some(&path), None) {
                Err(NotStarted::WouldNotLoad(reason)) => reason,
                Err(other) => panic!("{}: {other}", path.display()),
                Ok(_) => panic!("{} loaded as a policy", path.display()),
            };
            assert!(!reason.is_empty(), "{}", path.display());
        }
    }
}
