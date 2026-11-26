//! Shadow mode inside the real control loop.
//!
//! `shadow.rs` is tested with a stand-in candidate. That covers the queue, the counters and
//! the refusals, and none of the wiring: the load at startup, the offer on a driving tick, the
//! report published from the rate window, and the two IPC calls. All of that lives in
//! `control_loop`, so that is what these run — the function `main` spawns, on the fake robot
//! the rest of the suite uses, with real networks and a configured candidate.
//!
//! They need ONNX Runtime, like every test here that loads a shipped policy.

use super::*;
use duck_control::io::{JointTargets, Sensors, SlowSensors};

/// A policy shipped with the repository.
fn bundled(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../policies")
        .join(name)
        .join("policy.onnx")
}

/// The candidate under rehearsal. `SHADOW_CANDIDATE` names the file that is about to go to a
/// robot, so the rehearsal is run on exactly those bytes; unset, a shipped policy stands in.
/// Either way it must not be the live standing policy, which is `stand`.
fn shadow_candidate() -> PathBuf {
    std::env::var_os("SHADOW_CANDIDATE")
        .map(PathBuf::from)
        .unwrap_or_else(|| bundled("sitstand"))
}

/// Every slot stated, so nothing is looked up in a release directory a test machine does not
/// have: walk and stand are shipped policies, and the rest are switched off.
fn shipped_policy() -> params::PolicyParams {
    let mut policy = params::PolicyParams::default();
    policy.enabled = true;
    policy.set_slot(params::Slot::Walk, Some(bundled("walk")));
    policy.set_slot(params::Slot::Stand, Some(bundled("stand")));
    for slot in [
        params::Slot::SitStand,
        params::Slot::GroundPick,
        params::Slot::KickLeft,
        params::Slot::KickRight,
        params::Slot::Roulade,
    ] {
        policy.set_slot(slot, Some(PathBuf::from("none")));
    }
    policy
}

/// Whether a published label means the policy produced this tick's step.
///
/// `moving` cannot answer that: it is false for a policy standing still under no command,
/// which is exactly the state these runs settle into.
fn produces_steps(label: &str) -> bool {
    !matches!(label, "" | "held" | "homing" | "stuck_homing")
}

/// The newest frame the loop published, dropping whatever it superseded.
fn newest(
    published: &mut tokio::sync::broadcast::Receiver<Arc<proto::RobotState>>,
) -> Option<Arc<proto::RobotState>> {
    let mut last = None;
    loop {
        match published.try_recv() {
            Ok(frame) => last = Some(frame),
            Err(tokio::sync::broadcast::error::TryRecvError::Lagged(_)) => continue,
            Err(tokio::sync::broadcast::error::TryRecvError::Empty) => break,
            Err(tokio::sync::broadcast::error::TryRecvError::Closed) => {
                panic!("the loop stopped publishing its state")
            }
        }
    }
    last
}

/// The fake robot, keeping every target it is written so that two runs can be compared.
struct Recording<'a> {
    io: &'a mut FakeIo,
    targets: &'a mut Vec<JointTargets>,
}

impl RobotIo for Recording<'_> {
    fn read(&mut self) -> duck_control::io::Result<Sensors> {
        self.io.read()
    }

    fn write(&mut self, targets: &JointTargets) -> duck_control::io::Result<()> {
        self.targets.push(*targets);
        self.io.write(targets)
    }

    fn measures_velocity(&self) -> bool {
        self.io.measures_velocity()
    }

    fn measures_load(&self) -> bool {
        self.io.measures_load()
    }

    fn set_gain(&mut self, kp: u16) -> duck_control::io::Result<()> {
        self.io.set_gain(kp)
    }

    fn set_torque(&mut self, on: bool) -> duck_control::io::Result<()> {
        self.io.set_torque(on)
    }

    fn reboot(&mut self, id: u8) -> duck_control::io::Result<()> {
        self.io.reboot(id)
    }

    fn slow_sensors(&mut self) -> duck_control::io::Result<SlowSensors> {
        self.io.slow_sensors()
    }

    fn imu_stale(&self) -> duck_control::io::ImuStale {
        self.io.imu_stale()
    }

    fn imu_ready(&self) -> bool {
        self.io.imu_ready()
    }
}

/// One run of the loop, after it has stopped: what the fake robot was told, the label it was
/// last driven under, and the state the loop published.
struct Run {
    targets: Vec<JointTargets>,
    io: FakeIo,
    label: String,
    state: Arc<RobotState>,
}

/// A control loop that is running: the real `control_loop`, on a fake robot that is frozen —
/// the same standing pose and the same upright attitude on every read, whatever it is told —
/// with every target it is written kept.
struct Running {
    state: Arc<RobotState>,
    intents: Arc<Intents>,
    published: tokio::sync::broadcast::Receiver<Arc<proto::RobotState>>,
    task: tokio::task::JoinHandle<()>,
    results: std::sync::mpsc::Receiver<(FakeIo, Vec<JointTargets>)>,
}

fn start(policy: params::PolicyParams) -> Running {
    let mut params = Params::default();
    params.policy = policy;
    let state = Arc::new(RobotState::new(
        &params,
        Path::new("/test/robotd.toml"),
        false,
        false,
    ));
    // Subscribed before the first tick: the loop assembles a frame only for somebody.
    let published = state.state_tx.subscribe();
    let intents = Arc::new(Intents::new());
    let (tx, rx) = std::sync::mpsc::channel();
    let (loop_state, loop_intents) = (Arc::clone(&state), Arc::clone(&intents));
    let task = tokio::spawn(async move {
        let mut io = FakeIo::at(DEFAULT_POSITION).frozen();
        let mut targets = Vec::new();
        let recording = Recording {
            io: &mut io,
            targets: &mut targets,
        };
        control_loop(
            recording,
            loop_state,
            loop_intents,
            params,
            PathBuf::from("/test/robotd.toml"),
            Duration::from_millis(2),
            Arc::new(|| {}),
        )
        .await;
        tx.send((io, targets)).unwrap();
    });
    Running {
        state,
        intents,
        published,
        task,
        results: rx,
    }
}

/// Poll `what` until it holds. `FnMut`, because reading the loop's latest frame advances a
/// receiver.
async fn until(mut what: impl FnMut() -> bool, deadline: Duration, or: &str) {
    let started = Instant::now();
    while !what() {
        assert!(started.elapsed() < deadline, "{or}");
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
}

impl Running {
    /// The label on the loop's latest published frame.
    fn label(&mut self) -> String {
        newest(&mut self.published)
            .map(|frame| frame.policy.clone())
            .unwrap_or_default()
    }

    /// Home, then let the policy produce steps for at least `ticks` ticks and one whole
    /// report window.
    ///
    /// Each stage is asserted on its own: a loop that never homes, or homes and then holds,
    /// is a different finding from one that drives, and the comparison below is only worth
    /// anything if the policy really was producing the steps.
    async fn drive_for(&mut self, ticks: u64) {
        until(
            || self.state.ticks.load(Ordering::Relaxed) >= 5,
            Duration::from_secs(15),
            "no ticks",
        )
        .await;
        // What the pad's Start does: power the joints, ramp to home, then let the policy drive.
        // (`request_init` stands the robot up without ever starting the policy.)
        self.intents.set_enabled(true);
        until(
            || self.state.homed.load(Ordering::Relaxed),
            HOME_RAMP + Duration::from_secs(20),
            "init never reached home",
        )
        .await;
        let started = Instant::now();
        loop {
            let label = self.label();
            if produces_steps(&label) {
                break;
            }
            assert!(
                started.elapsed() < Duration::from_secs(20),
                "homed, but the policy never produced a step: label {label:?}, policy error {:?}",
                self.state.policy_error.load_full()
            );
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        // A fixed number of ticks after the first step, the same for every run. The filters
        // close on their fixed point slowly enough (see `SETTLED`) that two runs stopped a
        // different number of ticks apart differ by more than the comparison is meant to see.
        // Six hundred ticks is also at least 1.2 s at the 2 ms period, so a whole report
        // window has gone by.
        let from = self.state.ticks.load(Ordering::Relaxed);
        until(
            || self.state.ticks.load(Ordering::Relaxed) >= from + ticks,
            Duration::from_secs(120),
            "the loop did not keep ticking while the policy drove",
        )
        .await;
    }

    /// Stop the loop and take what it wrote.
    async fn stop(mut self) -> Run {
        let label = self.label();
        self.state.shutdown.store(true, Ordering::Relaxed);
        tokio::time::timeout(Duration::from_secs(30), self.task)
            .await
            .expect("the loop exits on shutdown")
            .expect("the loop task");
        let (io, targets) = self.results.recv().unwrap();
        Run {
            targets,
            io,
            label,
            state: self.state,
        }
    }
}

/// Home, drive for the same number of ticks every time, stop.
async fn drive(policy: params::PolicyParams) -> Run {
    let mut running = start(policy);
    running.drive_for(600).await;
    running.stop().await
}

/// Where the targets ended up, and how far the hundred before them were from it.
///
/// With a frozen robot and no command the targets close on one pose, and by the end they are
/// moving far slower than a servo resolves. The caller checks that against [`SETTLED`]: if
/// they were still moving by more, the loop would be doing something time-varying, and "two
/// runs agree" would be comparing wherever each happened to be.
fn ended(targets: &[JointTargets]) -> ([f64; NUM_JOINTS], f64) {
    let tail = &targets[targets.len().saturating_sub(100)..];
    assert!(
        tail.len() == 100,
        "only {} targets were written",
        targets.len()
    );
    let last = tail.last().unwrap().positions;
    let drift = tail
        .iter()
        .flat_map(|target| target.positions.iter().zip(last))
        .map(|(earlier, last)| (earlier - last).abs())
        .fold(0.0f64, f64::max);
    (last, drift)
}

/// The largest joint difference between two poses.
fn largest_difference(a: [f64; NUM_JOINTS], b: [f64; NUM_JOINTS]) -> f64 {
    a.iter().zip(b).map(|(a, b)| (a - b).abs()).fold(0.0f64, f64::max)
}

/// How still the last hundred targets must be for a run to count as settled. Measured on the
/// shipped walk policy: 1.3e-7 to 2.0e-7 rad over the hundred, depending on where in its slow
/// closing a run happens to stop. A servo count is about 1.5e-3 rad; this is 150 of those
/// below it, and 50 times the most that was measured.
const SETTLED: f64 = 1e-5;

/// Two runs are "the same pose" within this. Each is still closing on its fixed point at the
/// `SETTLED` rate and they do not stop on the same tick, so two runs of the *same*
/// configuration differ too. Measured, over repeated runs: 0 to 1e-7 rad between any two runs
/// — with a shadow, without one, with a refused candidate — all within that band. Anything the
/// shadow did to the driving policy would be a change in the policy's own arithmetic, which is
/// not a matter of this size.
const SAME_POSE: f64 = 1e-5;

fn call(id: u64, method: &str) -> serde_json::Value {
    serde_json::json!({"jsonrpc": "2.0", "id": id, "method": method})
}

/// The `result` of one reply.
fn result_of(line: &str) -> serde_json::Value {
    serde_json::from_str::<serde_json::Value>(line).unwrap()["result"].clone()
}

fn same_pose(a: [f64; NUM_JOINTS], b: [f64; NUM_JOINTS], what: &str) {
    for joint in 0..NUM_JOINTS {
        assert!(
            (a[joint] - b[joint]).abs() < SAME_POSE,
            "{what}: joint {joint} was driven to {} without the shadow and {} with it",
            a[joint],
            b[joint]
        );
    }
}

/// **A configured shadow runs inside the loop, and the robot is driven the same as without it.**
///
/// Two runs of the real loop on the same frozen robot: no candidate, then a candidate. Both
/// home and drive — otherwise the comparison is empty — and where they settle is the same pose
/// within [`SAME_POSE`] (two runs of one configuration differ by as much: see there), under
/// the same label, with the same torque and gain behind it. The second run's report is what a
/// deployment gate would read, so it is checked for naming the candidate and for having run
/// it.
#[tokio::test]
async fn a_configured_shadow_runs_in_the_loop_and_the_robot_is_driven_as_without_it() {
    let plain = drive(shipped_policy()).await;
    assert!(
        produces_steps(&plain.label),
        "the run without a shadow never had the policy driving: {:?}",
        plain.label
    );
    assert!(
        plain.state.shadow.load_full().is_none(),
        "no candidate configured, so nothing is shadowing"
    );
    assert!(
        plain.state.shadow_report.load_full().is_none(),
        "and no report: `None` is how a client tells 'nothing is shadowing' from 'shadowing, has measured nothing yet'"
    );

    let mut policy = shipped_policy();
    let candidate = shadow_candidate();
    policy.shadow_stand = Some(candidate.clone());
    let shadowed = drive(policy).await;
    assert!(
        produces_steps(&shadowed.label),
        "the run with a shadow never had the policy driving: {:?}",
        shadowed.label
    );
    assert_eq!(
        plain.label, shadowed.label,
        "the shadow changed which network drove the robot"
    );

    let digest = duck_control::policy::file_sha256(&candidate).unwrap();
    let handle = shadowed
        .state
        .shadow
        .load_full()
        .expect("a configured candidate starts a shadow");
    assert_eq!(
        handle.loaded(),
        Some((candidate.as_path(), digest.as_str())),
        "the shadow must be the configured file, identified by the digest of that file"
    );

    // The report the loop published once a second from inside its rate window. It is the
    // snapshot from before the loop stopped, so it still says the shadow was running.
    let report = shadowed
        .state
        .shadow_report
        .load_full()
        .expect("the loop publishes the report");
    let shown = candidate.display().to_string();
    assert_eq!(report.sha256.as_deref(), Some(digest.as_str()));
    assert_eq!(report.path.as_deref(), Some(shown.as_str()));
    assert_eq!(report.rate_hz, shadow::RATE_HZ);
    assert!(
        report.active,
        "it was running when this was published: {report:?}"
    );
    assert!(report.running_secs >= 1.0, "{report:?}");
    assert!(
        report.inferred >= 5,
        "the candidate was never really run: {report:?}"
    );
    assert_eq!(
        report.invalid, 0,
        "a shipped policy answers with 14 finite values: {report:?}"
    );
    assert_eq!(
        report.dropped_full, 0,
        "the worker kept up with the loop: {report:?}"
    );
    assert!(report.submitted >= report.inferred, "{report:?}");
    assert_eq!(
        report.diff_count, report.inferred,
        "every tick that offered a frame carried the driving policy's action: {report:?}"
    );
    assert!(
        report.diff_norm_mean.is_finite() && report.diff_norm_mean >= 0.0,
        "{report:?}"
    );

    // **And the robot did the same thing.**
    assert_eq!(plain.io.torque, shadowed.io.torque, "torque state");
    assert_eq!(
        plain.io.torque_writes, shadowed.io.torque_writes,
        "torque writes"
    );
    assert_eq!(plain.io.last_gain, shadowed.io.last_gain, "gain");
    let (without, plain_drift) = ended(&plain.targets);
    let (with, shadowed_drift) = ended(&shadowed.targets);
    assert!(
        plain_drift < SETTLED && shadowed_drift < SETTLED,
        "still moving: {plain_drift:e} without the shadow, {shadowed_drift:e} with it"
    );
    // Printed, not only asserted: the margin is the evidence, and it is only visible on a pass.
    eprintln!(
        "drift over the last hundred targets: {plain_drift:e} without the shadow, {shadowed_drift:e} with it; \
         largest joint difference between the two runs: {:.3e} (limit {SAME_POSE:e})",
        largest_difference(without, with)
    );
    same_pose(
        without,
        with,
        "the shadow changed what the robot was told",
    );
}

/// The two IPC calls against a loop that is running. With nothing shadowing the answer is
/// `null` — never an empty report, which reads as a shadow that has measured nothing.
/// `robot.shadowDisable` stops the shadow where it stands, and the loop does not notice.
#[tokio::test]
async fn the_report_and_disable_calls_reach_a_running_shadow() {
    let params = Params::default();
    let idle = Arc::new(RobotState::new(
        &params,
        Path::new("/test/robotd.toml"),
        false,
        false,
    ));
    for (id, method) in [(1, "robot.shadowReport"), (2, "robot.shadowDisable")] {
        let line = crate::tests::ipc_response(Arc::clone(&idle), call(id, method)).await;
        assert_eq!(
            result_of(&line),
            serde_json::Value::Null,
            "{method} with no shadow"
        );
    }

    let digest = duck_control::policy::file_sha256(&shadow_candidate()).unwrap();
    let mut policy = shipped_policy();
    policy.shadow_stand = Some(shadow_candidate());
    let mut running = start(policy);
    running.drive_for(60).await;
    let state = Arc::clone(&running.state);
    // The loop republishes the report once a second, so the one on the wire is up to a second
    // old: read straight after the first step, it is still the snapshot from before any frame
    // was offered. Wait for one that carries counts.
    until(
        || {
            state
                .shadow_report
                .load()
                .as_ref()
                .is_some_and(|r| r.inferred >= 5)
        },
        Duration::from_secs(10),
        "the loop never published a report with the candidate run",
    )
    .await;

    let line = crate::tests::ipc_response(Arc::clone(&state), call(3, "robot.shadowReport")).await;
    let report = result_of(&line);
    assert_eq!(report["sha256"], serde_json::json!(digest), "{report}");
    assert_eq!(report["active"], true, "{report}");
    assert_eq!(report["rate_hz"], serde_json::json!(shadow::RATE_HZ));
    assert!(
        report["inferred"]
            .as_u64()
            .is_some_and(|n| n >= 5),
        "the report must carry real counts: {report}"
    );

    // One key off: the handle stops at once, and the loop goes on driving.
    let line = crate::tests::ipc_response(Arc::clone(&state), call(4, "robot.shadowDisable")).await;
    assert_eq!(result_of(&line), serde_json::Value::Null);
    assert!(
        !state.shadow.load_full().unwrap().is_active(),
        "the handle is still active after robot.shadowDisable"
    );
    let at = state.ticks.load(Ordering::Relaxed);
    until(
        || state.ticks.load(Ordering::Relaxed) >= at + 100,
        Duration::from_secs(20),
        "the loop stopped ticking once the shadow was switched off",
    )
    .await;
    assert!(
        produces_steps(&running.label()),
        "the policy no longer drives after the shadow was switched off: {:?}",
        running.label()
    );

    // The next report says the shadow has stopped, and its counters stay where they were.
    until(
        || {
            state
                .shadow_report
                .load()
                .as_ref()
                .is_some_and(|r| !r.active)
        },
        Duration::from_secs(10),
        "the report never showed the shadow as stopped",
    )
    .await;
    let stopped = state.shadow_report.load_full().unwrap();
    assert!(stopped.running_secs > 0.0, "{stopped:?}");
    tokio::time::sleep(RATE_WINDOW + Duration::from_millis(300)).await;
    let line = crate::tests::ipc_response(Arc::clone(&state), call(5, "robot.shadowReport")).await;
    let again = result_of(&line);
    assert_eq!(again["active"], false, "{again}");
    assert_eq!(again["sha256"], serde_json::json!(digest), "{again}");
    assert_eq!(
        again["inferred"],
        serde_json::json!(stopped.inferred),
        "no inference after the shadow was switched off: {again}"
    );
    running.stop().await;
}

/// A candidate that may not run — the live standing policy by its own name, the same file
/// under another, or a file that is not there — leaves no shadow and costs the driving policy
/// nothing: the loop homes and drives to the same pose as with no candidate at all.
#[tokio::test]
async fn a_refused_or_unloadable_candidate_leaves_the_driving_policy_alone() {
    let plain = drive(shipped_policy()).await;
    assert!(produces_steps(&plain.label), "{:?}", plain.label);

    let temp = tempfile::tempdir().unwrap();
    let copy = temp.path().join("policy.onnx");
    std::fs::copy(bundled("stand"), &copy).unwrap();
    for candidate in [
        bundled("stand"),
        copy,
        PathBuf::from("/no/such/shadow/policy.onnx"),
    ] {
        let mut policy = shipped_policy();
        policy.shadow_stand = Some(candidate.clone());
        let refused = drive(policy).await;
        let what = candidate.display().to_string();
        assert!(produces_steps(&refused.label), "{what}: {:?}", refused.label);
        assert_eq!(plain.label, refused.label, "{what}: another network drove");
        assert!(
            refused.state.shadow.load_full().is_none(),
            "{what} started a shadow"
        );
        assert!(
            refused.state.shadow_report.load_full().is_none(),
            "{what} published a report"
        );
        assert_eq!(refused.io.torque, plain.io.torque, "{what}: torque state");
        assert_eq!(
            refused.io.torque_writes, plain.io.torque_writes,
            "{what}: torque writes"
        );
        let (without, plain_drift) = ended(&plain.targets);
        let (with, refused_drift) = ended(&refused.targets);
        assert!(
            plain_drift < SETTLED && refused_drift < SETTLED,
            "{what}: still moving: {plain_drift:e} plain, {refused_drift:e} refused"
        );
        eprintln!(
            "{what}: largest joint difference from the run with no candidate: {:.3e} (limit {SAME_POSE:e})",
            largest_difference(without, with)
        );
        same_pose(
            without,
            with,
            &format!("{what} changed what the robot was told"),
        );
    }
}
