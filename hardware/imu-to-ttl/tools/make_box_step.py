#!/usr/bin/env python3
"""Write a rectangular body-envelope STEP file (AP214, valid B-rep).

Used for the two parts KiCad ships no 3D model for: the 2.54 mm screw terminal
and the project-local LSM6DSV16X footprint. A box is the right level of detail
for a mechanical interference check.

Why this file is written by hand: the first version was a minimal STEP that
KiCad's OCCT importer silently discarded - the 3D viewer showed nothing and the
STEP export left the parts out (no warning either way). What a B-rep needs:

  * a millimetre LENGTH_UNIT inside a GEOMETRIC_REPRESENTATION_CONTEXT, and the
    ADVANCED_BREP_SHAPE_REPRESENTATION must reference that context;
  * a SHAPE_DEFINITION_REPRESENTATION tying the shape to the product graph;
  * faces bounded by EDGE_LOOPs made of ORIENTED_EDGEs over real EDGE_CURVEs -
    POLY_LOOP faces are legal in the schema but not universally importable;
  * every loop wound so that its right-hand normal points *out* of the solid,
    with the PLANE's axis along the same outward normal.

Usage:  python3 tools/make_box_step.py <out.step> <W> <D> <H> [x0] [y0]
        (millimetres; x0/y0 place the box relative to the footprint origin,
         Z starts at the board surface)
"""

import os
import sys


def box_step(path, w, d, h, x0, y0, label="body"):
    xs, ys, zs = (x0, x0 + w), (y0, y0 + d), (0.0, h)

    def P(i, j, k):
        return "#%d" % point_ids[(i, j, k)]

    out = ["ISO-10303-21;", "HEADER;",
           "FILE_DESCRIPTION(('%s envelope'),'2;1');" % label,
           "FILE_NAME('%s','2026-09-16T00:00:00',('imu-ttl'),('imu-ttl'),'','','');"
           % os.path.basename(path),
           "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
           "ENDSEC;", "DATA;"]

    n = 1
    def new():
        nonlocal n
        n += 1
        return n - 1

    # --- product graph
    app_ctx = new(); out.append("#%d=APPLICATION_CONTEXT('core data for automotive mechanical design processes');" % app_ctx)
    out.append("#%d=APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#%d);" % (new(), app_ctx))
    prod_ctx = new(); out.append("#%d=PRODUCT_CONTEXT('',#%d,'mechanical');" % (prod_ctx, app_ctx))
    prod = new(); out.append("#%d=PRODUCT('%s','%s','',(#%d));" % (prod, label, label, prod_ctx))
    pdf = new(); out.append("#%d=PRODUCT_DEFINITION_FORMATION('','',#%d);" % (pdf, prod))
    pd_ctx = new(); out.append("#%d=PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design');" % (pd_ctx, app_ctx))
    pd = new(); out.append("#%d=PRODUCT_DEFINITION('design','',#%d,#%d);" % (pd, pdf, pd_ctx))
    pds = new(); out.append("#%d=PRODUCT_DEFINITION_SHAPE('','',#%d);" % (pds, pd))

    # --- units and geometric context
    length_unit = new()
    out.append("#%d=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));" % length_unit)
    angle_unit = new()
    out.append("#%d=(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));" % angle_unit)
    solid_unit = new()
    out.append("#%d=(NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT());" % solid_unit)
    unc = new()
    out.append("#%d=UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#%d,"
               "'distance_accuracy_value','confusion accuracy');" % (unc, length_unit))
    geom_ctx = new()
    out.append("#%d=(GEOMETRIC_REPRESENTATION_CONTEXT(3) "
               "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d)) "
               "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d)) REPRESENTATION_CONTEXT('',''));"
               % (geom_ctx, unc, length_unit, angle_unit, solid_unit))

    # --- vertices
    point_ids = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                pid = new()
                out.append("#%d=CARTESIAN_POINT('',(%.4f,%.4f,%.4f));" % (pid, xs[i], ys[j], zs[k]))
                vid = new()
                out.append("#%d=VERTEX_POINT('',#%d);" % (vid, pid))
                point_ids[(i, j, k)] = vid

    # --- the 12 edges, each as a LINE from its first to its second endpoint
    def edge(a, b):
        pa = (xs[a[0]], ys[a[1]], zs[a[2]])
        pb = (xs[b[0]], ys[b[1]], zs[b[2]])
        vec = tuple(pb[t] - pa[t] for t in range(3))
        mag = sum(v * v for v in vec) ** 0.5
        pt = new(); out.append("#%d=CARTESIAN_POINT('',(%.4f,%.4f,%.4f));" % (pt, *pa))
        dr = new(); out.append("#%d=DIRECTION('',(%.6f,%.6f,%.6f));"
                               % (dr, *(v / mag for v in vec)))
        vc = new(); out.append("#%d=VECTOR('',#%d,%.4f);" % (vc, dr, mag))
        ln = new(); out.append("#%d=LINE('',#%d,#%d);" % (ln, pt, vc))
        ec = new(); out.append("#%d=EDGE_CURVE('',#%d,#%d,#%d,.T.);"
                               % (ec, point_ids[a], point_ids[b], ln))
        return ec

    V = {(i, j, k): (i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)}

    def face(corners, axis, ref):
        """corners: 4 corners in loop order (right-hand normal = axis, outward)"""
        oes = []
        for t in range(4):
            ec = edge(corners[t], corners[(t + 1) % 4])
            oe = new(); out.append("#%d=ORIENTED_EDGE('',*,*,#%d,.T.);" % (oe, ec))
            oes.append(oe)
        loop = new(); out.append("#%d=EDGE_LOOP('',(%s));"
                                 % (loop, ",".join("#%d" % o for o in oes)))
        bound = new(); out.append("#%d=FACE_OUTER_BOUND('',#%d,.T.);" % (bound, loop))
        org = new()
        o = (xs[corners[0][0]], ys[corners[0][1]], zs[corners[0][2]])
        out.append("#%d=CARTESIAN_POINT('',(%.4f,%.4f,%.4f));" % (org, *o))
        ax = new(); out.append("#%d=DIRECTION('',(%.1f,%.1f,%.1f));" % (ax, *axis))
        rf = new(); out.append("#%d=DIRECTION('',(%.1f,%.1f,%.1f));" % (rf, *ref))
        pl = new(); out.append("#%d=AXIS2_PLACEMENT_3D('',#%d,#%d,#%d);" % (pl, org, ax, rf))
        plane = new(); out.append("#%d=PLANE('',#%d);" % (plane, pl))
        af = new(); out.append("#%d=ADVANCED_FACE('',(#%d),#%d,.T.);" % (af, bound, plane))
        return af

    faces = [
        face([V[0, 0, 0], V[0, 1, 0], V[1, 1, 0], V[1, 0, 0]], (0, 0, -1), (1, 0, 0)),   # z=0
        face([V[0, 0, 1], V[1, 0, 1], V[1, 1, 1], V[0, 1, 1]], (0, 0, 1), (1, 0, 0)),    # z=H
        face([V[0, 0, 0], V[1, 0, 0], V[1, 0, 1], V[0, 0, 1]], (0, -1, 0), (1, 0, 0)),   # y=0
        face([V[0, 1, 0], V[0, 1, 1], V[1, 1, 1], V[1, 1, 0]], (0, 1, 0), (1, 0, 0)),    # y=D
        face([V[0, 0, 0], V[0, 0, 1], V[0, 1, 1], V[0, 1, 0]], (-1, 0, 0), (0, 1, 0)),   # x=0
        face([V[1, 0, 0], V[1, 1, 0], V[1, 1, 1], V[1, 0, 1]], (1, 0, 0), (0, 1, 0)),    # x=W
    ]
    shell = new(); out.append("#%d=CLOSED_SHELL('',(%s));"
                              % (shell, ",".join("#%d" % f for f in faces)))
    brep = new(); out.append("#%d=MANIFOLD_SOLID_BREP('%s',#%d);" % (brep, label, shell))
    absr = new(); out.append("#%d=ADVANCED_BREP_SHAPE_REPRESENTATION('',(#%d),#%d);"
                             % (absr, brep, geom_ctx))
    out.append("#%d=SHAPE_DEFINITION_REPRESENTATION(#%d,#%d);" % (new(), pds, absr))
    out += ["ENDSEC;", "END-ISO-10303-21;"]

    open(path, "w").write("\n".join(out) + "\n")
    return len([l for l in out if l.startswith("#")])


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    out_path, bw, bd, bh = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    bx = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    by = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    ents = box_step(out_path, bw, bd, bh, bx, by,
                    label=os.path.splitext(os.path.basename(out_path))[0])
    print("wrote %s (%.1f x %.1f x %.1f mm, %d entities)" % (out_path, bw, bd, bh, ents))
