









(function (global) {
  'use strict';
  const M = () => Scene3D.M;
  const deg = d => d * Math.PI / 180;


  function compose(pos, rot, s) {
    const [rx, ry, rz] = (rot || [0, 0, 0]).map(deg);
    const cx=Math.cos(rx), sx=Math.sin(rx), cy=Math.cos(ry), sy=Math.sin(ry),
          cz=Math.cos(rz), sz=Math.sin(rz);
    const R = [
      cz*cy,            sz*cy,           -sy,
      cz*sy*sx - sz*cx, sz*sy*sx + cz*cx, cy*sx,
      cz*sy*cx + sz*sx, sz*sy*cx - cz*sx, cy*cx,
    ];
    const m = new Float32Array(16);
    for (let c = 0; c < 3; c++) for (let r = 0; r < 3; r++) m[c*4+r] = R[c*3+r] * s;
    m[12]=pos[0]; m[13]=pos[1]; m[14]=pos[2]; m[15]=1;
    return m;
  }




  function terrainZAt(t, x, y) {
    if (!t || !t.nx) return null;
    const fx = (x - t.x0) / (t.x1 - t.x0) * (t.nx - 1);
    const fy = (y - t.y0) / (t.y1 - t.y0) * (t.ny - 1);
    if (!(fx >= 0 && fy >= 0 && fx <= t.nx - 1 && fy <= t.ny - 1)) return null;
    const i0 = Math.floor(fx), j0 = Math.floor(fy);
    const i1 = Math.min(i0 + 1, t.nx - 1), j1 = Math.min(j0 + 1, t.ny - 1);
    const u = fx - i0, v = fy - j0;
    const Z = (i, j) => t.verts[(j * t.nx + i) * 3 + 2];
    return (Z(i0,j0)*(1-u) + Z(i1,j0)*u) * (1-v)
         + (Z(i0,j1)*(1-u) + Z(i1,j1)*u) * v;
  }


  function gridLines(x0, x1, y0, y1, z, step) {
    const s = [];
    for (let x = Math.ceil(x0/step)*step; x <= x1; x += step) s.push(x,y0,z, x,y1,z);
    for (let y = Math.ceil(y0/step)*step; y <= y1; y += step) s.push(x0,y,z, x1,y,z);
    return s;
  }


  class TerrainView {
    constructor(canvas) {
      this.sc = new Scene3D(canvas, { dist: 400, pitch: 0.5, yaw: -2.2, fogK: 0.0011 });
      this.meshCache = new Map();
      this.selected = -1;
      this.showBeam = false;
    }

    async update(tmesh, d, scene, opts = {}) {
      const sc = this.sc; if (sc.dead) return;
      sc.clear();
      if (!tmesh || !tmesh.verts) {

        const zb = opts.seabed_top_m ?? -20, E = 150;
        const b = [];
        b.push(-E,-E,zb, E,-E,zb, E,E,zb, -E,-E,zb, E,E,zb, -E,E,zb);
        sc.addMesh(b, null, [0.42,0.36,0.24,1], { flat: false });
        sc.addLines(gridLines(-E,E,-E,E,zb+0.05,25), [1,1,1,0.10]);
        this._overlays(d, scene, zb, E, null, opts);
        sc.frame([0,0,zb], E * 1.1);
        return;
      }
      sc.addMesh(tmesh.verts, tmesh.faces, null, { vcolors: tmesh.colors, flat: true });
      const zb = tmesh.zmax;
      const ex = Math.max(tmesh.x1 - tmesh.x0, tmesh.y1 - tmesh.y0) / 2;

      const w = [];
      w.push(tmesh.x0,tmesh.y0,0, tmesh.x1,tmesh.y0,0, tmesh.x1,tmesh.y1,0,
             tmesh.x0,tmesh.y0,0, tmesh.x1,tmesh.y1,0, tmesh.x0,tmesh.y1,0);
      sc.addMesh(w, null, [0.16,0.42,0.62,0.13], { flat: false });

      await this._overlays(d, scene, zb, ex, tmesh, opts);
      sc.frame([(tmesh.x0+tmesh.x1)/2, (tmesh.y0+tmesh.y1)/2, (tmesh.zmin+tmesh.zmax)/2], ex * 1.15);
    }

    async _overlays(d, scene, zb, ex, tmesh, opts = {}) {
      const sc = this.sc;

      if (d?.legs?.length) {
        const alt = d.altitude_m;


        const zs = (d.sensor_z_m !== undefined) ? d.sensor_z_m : (zb + alt);
        const hs = d.swath_m || 50;
        const track = [], ribbon = [];
        d.legs.forEach(L => {
          track.push(L.p0[0], L.p0[1], zs, L.p1[0], L.p1[1], zs);
          const dx = L.p1[0]-L.p0[0], dy = L.p1[1]-L.p0[1], len = Math.hypot(dx,dy)||1;
          const nx = -dy/len, ny = dx/len;
          const zr = (tmesh ? tmesh.zmax : zb) + 0.4;
          const A=[L.p0[0]+nx*hs, L.p0[1]+ny*hs], B=[L.p1[0]+nx*hs, L.p1[1]+ny*hs];
          const C=[L.p1[0]-nx*hs, L.p1[1]-ny*hs], D=[L.p0[0]-nx*hs, L.p0[1]-ny*hs];
          ribbon.push(A[0],A[1],zr, B[0],B[1],zr, C[0],C[1],zr,
                      A[0],A[1],zr, C[0],C[1],zr, D[0],D[1],zr);

          track.push(L.p0[0],L.p0[1],zs, L.p0[0],L.p0[1],zb);
        });
        sc.addMesh(ribbon, null, [0.29,0.66,1.0,0.16], { flat: false, alpha: true });
        sc.addLines(track, [0.98,0.99,1.0,0.95]);









        if (this.showBeam) {
          const lo = Math.max(d.beam_lo_deg, 0.2), hi = Math.min(d.beam_hi_deg, 89.8);
          const rMin = Math.max(opts.range_min_m ?? 0.5, 0.01);
          const rMax = opts.range_max_m ?? (d.slant_far_m || hs);
          const NS = 5, NA = 16, MARCH = 90;
          const vol = [], edge = [], hitPts = [], missPts = [], innerArc = [];


          const cast = (px, py, nx, ny, th) => {
            const c = Math.cos(th), sn = Math.sin(th);
            const zFlat = tmesh ? null : zb;
            let prev = rMin, prevAbove = true;
            for (let m = 1; m <= MARCH; m++) {
              const R = rMin + (rMax - rMin) * m / MARCH;
              const X = px + nx * R * c, Y = py + ny * R * c, Z = zs - R * sn;
              const g = tmesh ? terrainZAt(tmesh, X, Y) : zFlat;



              if (g === null) return { R: prev, hit: false, offGrid: true };
              const above = Z > g;
              if (!above) {

                let a = prev, b = R;
                for (let it = 0; it < 12; it++) {
                  const mid = (a + b) / 2;
                  const Xm = px + nx*mid*c, Ym = py + ny*mid*c, Zm = zs - mid*sn;
                  const gm = tmesh ? terrainZAt(tmesh, Xm, Ym) : zFlat;
                  if (gm !== null && Zm <= gm) b = mid; else a = mid;
                }
                return { R: b, hit: true };
              }
              prev = R; prevAbove = above;
            }
            return { R: rMax, hit: false };
          };

          d.legs.forEach(L => {
            const dx = L.p1[0]-L.p0[0], dy = L.p1[1]-L.p0[1], len = Math.hypot(dx,dy)||1;
            const nx0 = -dy/len, ny0 = dx/len;
            for (let k = 0; k <= NS; k++) {
              const t = k / NS;
              const px = L.p0[0] + dx*t, py = L.p0[1] + dy*t;
              [1,-1].forEach(side => {
                const nx = nx0*side, ny = ny0*side;
                const far = [], near = [];
                for (let i = 0; i <= NA; i++) {
                  const th = deg(lo + (hi-lo)*i/NA);
                  const c = Math.cos(th), sn = Math.sin(th);
                  const r = cast(px, py, nx, ny, th);
                  far.push([px + nx*r.R*c, py + ny*r.R*c, zs - r.R*sn, r.hit]);
                  near.push([px + nx*rMin*c, py + ny*rMin*c, zs - rMin*sn]);
                  (r.hit ? hitPts : missPts).push(far[i][0], far[i][1], far[i][2]);
                }

                for (let i = 0; i < NA; i++) {
                  const a0=near[i], a1=near[i+1], b0=far[i], b1=far[i+1];
                  vol.push(a0[0],a0[1],a0[2], b0[0],b0[1],b0[2], b1[0],b1[1],b1[2]);
                  vol.push(a0[0],a0[1],a0[2], b1[0],b1[1],b1[2], a1[0],a1[1],a1[2]);
                  innerArc.push(a0[0],a0[1],a0[2], a1[0],a1[1],a1[2]);
                }

                [0, NA].forEach(i => {
                  edge.push(near[i][0],near[i][1],near[i][2], far[i][0],far[i][1],far[i][2]);
                  edge.push(px, py, zs, near[i][0],near[i][1],near[i][2]);
                });
              });
            }
          });
          sc.addMesh(vol, null, [0.15,0.85,0.95,0.11], { flat:false, alpha:true });
          sc.addLines(edge, [0.30,0.92,1.0,0.55]);
          sc.addLines(innerArc, [1.0,0.62,0.10,0.75]);

          const dots = (arr, col) => {
            if (!arr.length) return;
            const seg = [];
            for (let i = 0; i + 2 < arr.length; i += 3)
              seg.push(arr[i],arr[i+1],arr[i+2], arr[i],arr[i+1],arr[i+2]+1.2);
            sc.addLines(seg, col);
          };
          dots(hitPts, [0.25,0.95,0.45,0.95]);
          dots(missPts, [1.0,0.25,0.28,0.95]);
          this.beamStats = {
            hit: hitPts.length/3, miss: missPts.length/3,
            rMin, rMax, lo, hi,
          };
        } else {
          this.beamStats = null;
        }
      }










      if (scene?.objects?.length) {
        for (const o of scene.objects) {
          const m = await this._mesh(o.catalog_id, o._final_size_m);
          const isWreck = (o.tags||[]).includes('wreck');
          const col = isWreck ? [0.86,0.70,0.32,1] : [0.62,0.66,0.72,1];
          const z = o.position_m[2];
          const sel = scene.objects.indexOf(o) === this.selected;
          if (m && m.verts) {



            const hz = (m.bbox_m ? m.bbox_m[2] : 0);
            const zb2 = z - hz * (o.burial_ratio || 0);
            const model = compose([o.position_m[0], o.position_m[1], zb2], o.rotation_deg,
                                  o._final_size_m ? 1.0 : o.scale);
            sc.addMesh(m.verts, m.faces, sel ? [1.0, 0.22, 0.26, 1] : col,
                       { model, flat: true, tag: o.catalog_id });
            if (sel) {

              const b = m.bbox_m || [4,4,4];
              const P=[[-b[0]/2,-b[1]/2,-b[2]/2],[b[0]/2,-b[1]/2,-b[2]/2],
                       [b[0]/2,b[1]/2,-b[2]/2],[-b[0]/2,b[1]/2,-b[2]/2],
                       [-b[0]/2,-b[1]/2,b[2]/2],[b[0]/2,-b[1]/2,b[2]/2],
                       [b[0]/2,b[1]/2,b[2]/2],[-b[0]/2,b[1]/2,b[2]/2]];
              const E=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
              const seg=[]; E.forEach(([a,c2])=>seg.push(...P[a],...P[c2]));
              sc.addLines(seg, [1.0,0.25,0.28,1], { model });
              const r = Math.max(...b) * 0.9;
              const mark=[];
              for (let i=0;i<24;i++){
                const t0=i/24*Math.PI*2, t1=(i+1)/24*Math.PI*2;
                mark.push(o.position_m[0]+r*Math.cos(t0), o.position_m[1]+r*Math.sin(t0), z+0.3,
                          o.position_m[0]+r*Math.cos(t1), o.position_m[1]+r*Math.sin(t1), z+0.3);
              }
              mark.push(o.position_m[0], o.position_m[1], z, o.position_m[0], o.position_m[1], z + r*1.6);
              sc.addLines(mark, [1.0,0.30,0.32,0.95]);
            }
          } else {
            sc.addMesh(Scene3D.box([o.position_m[0],o.position_m[1],z+2],[6,6,4]), null, col);
          }
        }
      }
    }

    async _mesh(id, sizeM) {
      const key = id + '@' + (sizeM || 0);
      if (this.meshCache.has(key)) return this.meshCache.get(key);
      const p = fetch('/api/mesh?id=' + encodeURIComponent(id)
                      + (sizeM ? '&size=' + sizeM : ''))
        .then(r => r.json()).then(m => m.error ? null : m).catch(() => null);
      this.meshCache.set(key, p);
      return p;
    }
  }


  class ObjectView {
    constructor(canvas) {
      this.sc = new Scene3D(canvas, { dist: 4, pitch: 0.35, yaw: -0.9 });
    }
    show(m, color) {
      const sc = this.sc; if (sc.dead) return;
      sc.clear();
      if (!m || !m.verts) return;
      sc.addMesh(m.verts, m.faces, color || [0.80,0.82,0.86,1], { flat: true });
      const b = m.bbox_m;
      const r = Math.max(...b) / 2;

      const g = Math.max(r * 1.6, 0.5);
      sc.addLines(gridLines(-g, g, -g, g, -b[2]/2, g/5), [1,1,1,0.13]);

      const [X,Y,Z] = b.map(v=>v/2);
      const P = [[-X,-Y,-Z],[X,-Y,-Z],[X,Y,-Z],[-X,Y,-Z],[-X,-Y,Z],[X,-Y,Z],[X,Y,Z],[-X,Y,Z]];
      const E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
      const seg = []; E.forEach(([a,b2]) => seg.push(...P[a], ...P[b2]));
      sc.addLines(seg, [0.29,0.66,1.0,0.5]);
      sc.frame([0,0,0], r * 1.5);
    }
  }

  global.Viz3D = { TerrainView, ObjectView, compose };
})(window);
