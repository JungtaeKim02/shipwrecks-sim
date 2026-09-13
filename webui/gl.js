








(function (global) {
  'use strict';

  const VS = `
attribute vec3 aPos; attribute vec3 aNrm; attribute vec4 aCol;
uniform mat4 uMVP; uniform mat4 uModel;
varying vec3 vNrm; varying vec4 vCol; varying vec3 vWorld;
void main(){
  vNrm = mat3(uModel) * aNrm;
  vCol = aCol;
  vWorld = (uModel * vec4(aPos,1.0)).xyz;
  gl_Position = uMVP * vec4(aPos,1.0);
}`;

  const FS = `
precision mediump float;
varying vec3 vNrm; varying vec4 vCol; varying vec3 vWorld;
uniform vec3 uLight; uniform float uFlat; uniform float uFogK; uniform vec3 uFog;
void main(){
  vec3 n = normalize(vNrm);
  float d = abs(dot(n, normalize(uLight)));
  float lit = mix(1.0, 0.30 + 0.70*d, uFlat);
  vec3 c = vCol.rgb * lit;
  float f = clamp(exp(-uFogK * length(vWorld)), 0.0, 1.0);
  c = mix(uFog, c, f);
  gl_FragColor = vec4(c, vCol.a);
}`;


  const M = {
    ident: () => new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]),
    mul(a, b) {
      const o = new Float32Array(16);
      for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
        let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
        o[i * 4 + j] = s;
      }
      return o;
    },
    persp(fovy, asp, zn, zf) {
      const f = 1 / Math.tan(fovy / 2), nf = 1 / (zn - zf);
      return new Float32Array([f / asp,0,0,0, 0,f,0,0, 0,0,(zf+zn)*nf,-1, 0,0,2*zf*zn*nf,0]);
    },
    lookAt(e, c, up) {
      const z = norm(sub(e, c)), x = norm(cross(up, z)), y = cross(z, x);
      return new Float32Array([
        x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0,
        -dot(x,e), -dot(y,e), -dot(z,e), 1]);
    },
    trans(t) { const m = M.ident(); m[12]=t[0]; m[13]=t[1]; m[14]=t[2]; return m; },

    invert(m) {
      const i = new Float32Array(16), a = m;
      i[0]  =  a[5]*a[10]*a[15] - a[5]*a[11]*a[14] - a[9]*a[6]*a[15] + a[9]*a[7]*a[14] + a[13]*a[6]*a[11] - a[13]*a[7]*a[10];
      i[4]  = -a[4]*a[10]*a[15] + a[4]*a[11]*a[14] + a[8]*a[6]*a[15] - a[8]*a[7]*a[14] - a[12]*a[6]*a[11] + a[12]*a[7]*a[10];
      i[8]  =  a[4]*a[9]*a[15]  - a[4]*a[11]*a[13] - a[8]*a[5]*a[15] + a[8]*a[7]*a[13] + a[12]*a[5]*a[11] - a[12]*a[7]*a[9];
      i[12] = -a[4]*a[9]*a[14]  + a[4]*a[10]*a[13] + a[8]*a[5]*a[14] - a[8]*a[6]*a[13] - a[12]*a[5]*a[10] + a[12]*a[6]*a[9];
      i[1]  = -a[1]*a[10]*a[15] + a[1]*a[11]*a[14] + a[9]*a[2]*a[15] - a[9]*a[3]*a[14] - a[13]*a[2]*a[11] + a[13]*a[3]*a[10];
      i[5]  =  a[0]*a[10]*a[15] - a[0]*a[11]*a[14] - a[8]*a[2]*a[15] + a[8]*a[3]*a[14] + a[12]*a[2]*a[11] - a[12]*a[3]*a[10];
      i[9]  = -a[0]*a[9]*a[15]  + a[0]*a[11]*a[13] + a[8]*a[1]*a[15] - a[8]*a[3]*a[13] - a[12]*a[1]*a[11] + a[12]*a[3]*a[9];
      i[13] =  a[0]*a[9]*a[14]  - a[0]*a[10]*a[13] - a[8]*a[1]*a[14] + a[8]*a[2]*a[13] + a[12]*a[1]*a[10] - a[12]*a[2]*a[9];
      i[2]  =  a[1]*a[6]*a[15]  - a[1]*a[7]*a[14]  - a[5]*a[2]*a[15] + a[5]*a[3]*a[14] + a[13]*a[2]*a[7]  - a[13]*a[3]*a[6];
      i[6]  = -a[0]*a[6]*a[15]  + a[0]*a[7]*a[14]  + a[4]*a[2]*a[15] - a[4]*a[3]*a[14] - a[12]*a[2]*a[7]  + a[12]*a[3]*a[6];
      i[10] =  a[0]*a[5]*a[15]  - a[0]*a[7]*a[13]  - a[4]*a[1]*a[15] + a[4]*a[3]*a[13] + a[12]*a[1]*a[7]  - a[12]*a[3]*a[5];
      i[14] = -a[0]*a[5]*a[14]  + a[0]*a[6]*a[13]  + a[4]*a[1]*a[14] - a[4]*a[2]*a[13] - a[12]*a[1]*a[6]  + a[12]*a[2]*a[5];
      i[3]  = -a[1]*a[6]*a[11]  + a[1]*a[7]*a[10]  + a[5]*a[2]*a[11] - a[5]*a[3]*a[10] - a[9]*a[2]*a[7]   + a[9]*a[3]*a[6];
      i[7]  =  a[0]*a[6]*a[11]  - a[0]*a[7]*a[10]  - a[4]*a[2]*a[11] + a[4]*a[3]*a[10] + a[8]*a[2]*a[7]   - a[8]*a[3]*a[6];
      i[11] = -a[0]*a[5]*a[11]  + a[0]*a[7]*a[9]   + a[4]*a[1]*a[11] - a[4]*a[3]*a[9]  - a[8]*a[1]*a[7]   + a[8]*a[3]*a[5];
      i[15] =  a[0]*a[5]*a[10]  - a[0]*a[6]*a[9]   - a[4]*a[1]*a[10] + a[4]*a[2]*a[9]  + a[8]*a[1]*a[6]   - a[8]*a[2]*a[5];
      let det = a[0]*i[0] + a[1]*i[4] + a[2]*i[8] + a[3]*i[12];
      if (!det) return null;
      det = 1.0 / det;
      for (let k = 0; k < 16; k++) i[k] *= det;
      return i;
    },
  };
  const sub = (a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const norm=a=>{const l=Math.hypot(...a)||1; return [a[0]/l,a[1]/l,a[2]/l];};


  class Scene3D {
    constructor(canvas, opts = {}) {
      this.cv = canvas;

      const gl = this.gl = canvas.getContext('webgl', {
        antialias: true, alpha: !!opts.alpha,
        preserveDrawingBuffer: !!opts.keepBuffer });
      if (!gl) { this.dead = true; return; }
      this.prog = this._program(VS, FS);
      this.loc = {
        aPos: gl.getAttribLocation(this.prog, 'aPos'),
        aNrm: gl.getAttribLocation(this.prog, 'aNrm'),
        aCol: gl.getAttribLocation(this.prog, 'aCol'),
        uMVP: gl.getUniformLocation(this.prog, 'uMVP'),
        uModel: gl.getUniformLocation(this.prog, 'uModel'),
        uLight: gl.getUniformLocation(this.prog, 'uLight'),
        uFlat: gl.getUniformLocation(this.prog, 'uFlat'),
        uFogK: gl.getUniformLocation(this.prog, 'uFogK'),
        uFog: gl.getUniformLocation(this.prog, 'uFog'),
      };
      this.objects = [];
      this.userMoved = false;
      this._lastFrame = null;
      this.bg = opts.bg || [0.043, 0.059, 0.082];
      this.fogK = opts.fogK ?? 0.0;

      this.cam = { yaw: opts.yaw ?? -0.9, pitch: opts.pitch ?? 0.55,
                   dist: opts.dist ?? 200, target: opts.target || [0, 0, 0] };
      this._bindControls();
      this._resize();
      new ResizeObserver(() => { this._resize(); this.draw(); }).observe(canvas);
    }

    _program(vs, fs) {
      const gl = this.gl, p = gl.createProgram();
      for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
        const s = gl.createShader(type);
        gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
          throw new Error(gl.getShaderInfoLog(s));
        gl.attachShader(p, s);
      }
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    }

    _resize() {
      const r = this.cv.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(r.width * dpr));
      const h = Math.max(1, Math.round((r.height || 300) * dpr));
      if (this.cv.width !== w || this.cv.height !== h) { this.cv.width = w; this.cv.height = h; }
    }

    _bindControls() {
      let drag = null;
      this.cv.addEventListener('pointerdown', e => {
        drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 1 };
        this.cv.setPointerCapture(e.pointerId);
      });
      this.cv.addEventListener('pointermove', e => {
        if (!drag) return;
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.x = e.clientX; drag.y = e.clientY;
        this.userMoved = true;
        if (drag.pan) {
          const s = this.cam.dist * 0.0016;
          const c = Math.cos(this.cam.yaw), si = Math.sin(this.cam.yaw);
          this.cam.target[0] -= (c * dx) * s;
          this.cam.target[1] -= (-si * dx) * s;
          this.cam.target[2] += dy * s;
        } else {
          this.cam.yaw -= dx * 0.008;
          this.cam.pitch = Math.max(-1.45, Math.min(1.45, this.cam.pitch + dy * 0.008));
        }
        this.draw();
      });
      const stop = e => { if (drag) this.cv.releasePointerCapture(e.pointerId); drag = null; };
      this.cv.addEventListener('pointerup', stop);
      this.cv.addEventListener('pointercancel', stop);
      this.cv.addEventListener('wheel', e => {
        e.preventDefault();
        this.userMoved = true;
        this.cam.dist *= Math.exp(e.deltaY * 0.0012);
        this.draw();
      }, { passive: false });
    }

    clear() {
      const gl = this.gl;
      this.objects.forEach(o => { gl.deleteBuffer(o.vb); if (o.ib) gl.deleteBuffer(o.ib); });
      this.objects = [];
    }



    addMesh(verts, faces, color, opt = {}) {
      const gl = this.gl;
      const V = verts instanceof Float32Array ? verts : new Float32Array(verts);
      const F = faces ? (faces instanceof Uint32Array || faces instanceof Uint16Array
        ? faces : new Uint32Array(faces)) : null;
      const n = F ? F.length : V.length / 3;


      const out = new Float32Array(n * 10);
      const vc = opt.vcolors || null;
      for (let t = 0; t < n; t += 3) {
        const i0 = F ? F[t] : t, i1 = F ? F[t + 1] : t + 1, i2 = F ? F[t + 2] : t + 2;
        const p = [[V[i0*3],V[i0*3+1],V[i0*3+2]],[V[i1*3],V[i1*3+1],V[i1*3+2]],
                   [V[i2*3],V[i2*3+1],V[i2*3+2]]];
        const nm = norm(cross(sub(p[1],p[0]), sub(p[2],p[0])));
        for (let k = 0; k < 3; k++) {
          const o = (t + k) * 10, idx = [i0,i1,i2][k];
          out[o]=p[k][0]; out[o+1]=p[k][1]; out[o+2]=p[k][2];
          out[o+3]=nm[0]; out[o+4]=nm[1]; out[o+5]=nm[2];
          const c = vc ? [vc[idx*4],vc[idx*4+1],vc[idx*4+2],vc[idx*4+3]] : color;
          out[o+6]=c[0]; out[o+7]=c[1]; out[o+8]=c[2]; out[o+9]=c[3];
        }
      }
      const vb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, vb);
      gl.bufferData(gl.ARRAY_BUFFER, out, gl.STATIC_DRAW);
      const o = { vb, count: n, mode: gl.TRIANGLES, alpha: (color?.[3] ?? 1) < 1 || opt.alpha,
                  flat: opt.flat === false ? 0 : 1, model: opt.model || M.ident(),
                  hidden: false, tag: opt.tag };
      this.objects.push(o);
      return o;
    }


    addLines(segs, color, opt = {}) {
      const gl = this.gl;
      const n = segs.length / 3;
      const out = new Float32Array(n * 10);
      for (let i = 0; i < n; i++) {
        const o = i * 10;
        out[o]=segs[i*3]; out[o+1]=segs[i*3+1]; out[o+2]=segs[i*3+2];
        out[o+3]=0; out[o+4]=0; out[o+5]=1;
        out[o+6]=color[0]; out[o+7]=color[1]; out[o+8]=color[2]; out[o+9]=color[3]??1;
      }
      const vb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, vb);
      gl.bufferData(gl.ARRAY_BUFFER, out, gl.STATIC_DRAW);
      const o = { vb, count: n, mode: gl.LINES, alpha: (color[3]??1) < 1,
                  flat: 0, model: opt.model || M.ident(), hidden: false, tag: opt.tag };
      this.objects.push(o);
      return o;
    }


    static box(c, s) {
      const [x,y,z]=c, [a,b,d]=[s[0]/2,s[1]/2,s[2]/2];
      const P=[[x-a,y-b,z-d],[x+a,y-b,z-d],[x+a,y+b,z-d],[x-a,y+b,z-d],
               [x-a,y-b,z+d],[x+a,y-b,z+d],[x+a,y+b,z+d],[x-a,y+b,z+d]];
      const Q=[[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[0,3,7,4]];
      const v=[];
      Q.forEach(q=>{[[0,1,2],[0,2,3]].forEach(t=>t.forEach(k=>v.push(...P[q[k]])));});
      return v;
    }








    frame(center, radius, force = false) {
      this._lastFrame = [center.slice(), radius];
      if (this.userMoved && !force) { this.draw(); return; }
      this.cam.target = center.slice();
      this.cam.dist = radius * 2.4;
      this.draw();
    }


    resetView() {
      this.userMoved = false;
      if (this._lastFrame) this.frame(this._lastFrame[0], this._lastFrame[1], true);
      else this.draw();
    }



    refresh() {
      if (this.dead) return;
      const go = () => { this._resize(); this.draw(); };
      go();
      requestAnimationFrame(() => { go(); requestAnimationFrame(go); });
    }

    draw() {
      if (this.dead) return;
      const gl = this.gl;
      gl.viewport(0, 0, this.cv.width, this.cv.height);
      gl.clearColor(...this.bg, 1);
      gl.enable(gl.DEPTH_TEST);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(this.prog);

      const c = this.cam;

      const cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
      const eye = [c.target[0] + c.dist * cp * Math.cos(c.yaw),
                   c.target[1] + c.dist * cp * Math.sin(c.yaw),
                   c.target[2] + c.dist * sp];
      const view = M.lookAt(eye, c.target, [0, 0, 1]);
      const proj = M.persp(0.9, this.cv.width / this.cv.height,
                           Math.max(c.dist * 0.002, 0.05), c.dist * 12 + 1000);
      const VP = M.mul(proj, view);

      gl.uniform3fv(this.loc.uLight, new Float32Array(norm([0.4, 0.5, 1.0])));
      gl.uniform1f(this.loc.uFogK, this.fogK);
      gl.uniform3fv(this.loc.uFog, new Float32Array(this.bg));


      const pass = (alpha) => {
        gl.depthMask(!alpha);
        if (alpha) { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); }
        else gl.disable(gl.BLEND);
        for (const o of this.objects) {
          if (o.hidden || !!o.alpha !== alpha) continue;
          gl.bindBuffer(gl.ARRAY_BUFFER, o.vb);
          const st = 40;
          gl.enableVertexAttribArray(this.loc.aPos);
          gl.vertexAttribPointer(this.loc.aPos, 3, gl.FLOAT, false, st, 0);
          gl.enableVertexAttribArray(this.loc.aNrm);
          gl.vertexAttribPointer(this.loc.aNrm, 3, gl.FLOAT, false, st, 12);
          gl.enableVertexAttribArray(this.loc.aCol);
          gl.vertexAttribPointer(this.loc.aCol, 4, gl.FLOAT, false, st, 24);
          gl.uniformMatrix4fv(this.loc.uModel, false, o.model);
          gl.uniformMatrix4fv(this.loc.uMVP, false, M.mul(VP, o.model));
          gl.uniform1f(this.loc.uFlat, o.flat);
          gl.drawArrays(o.mode, 0, o.count);
        }
      };
      pass(false); pass(true);
      gl.depthMask(true);



      this._vp = VP;
      if (this.onDraw) this.onDraw(this);
    }


    rayFromScreen(sx, sy) {
      if (!this._vp) return null;
      const inv = M.invert(this._vp);
      if (!inv) return null;
      const r = this.cv.getBoundingClientRect();
      const nx = (sx / r.width) * 2 - 1, ny = 1 - (sy / r.height) * 2;
      const un = (z) => {
        const w = inv[3]*nx + inv[7]*ny + inv[11]*z + inv[15];
        return [(inv[0]*nx + inv[4]*ny + inv[8]*z  + inv[12]) / w,
                (inv[1]*nx + inv[5]*ny + inv[9]*z  + inv[13]) / w,
                (inv[2]*nx + inv[6]*ny + inv[10]*z + inv[14]) / w];
      };
      const a = un(-1), b = un(1);
      const d = norm(sub(b, a));
      return { o: a, d };
    }


    static hitPlaneZ(ray, z) {
      if (!ray || Math.abs(ray.d[2]) < 1e-6) return null;
      const t = (z - ray.o[2]) / ray.d[2];
      if (t < 0) return null;
      return [ray.o[0] + ray.d[0]*t, ray.o[1] + ray.d[1]*t, z];
    }


    project(p) {
      if (!this._vp) return { visible: false };
      const m = this._vp;
      const w = m[3]*p[0] + m[7]*p[1] + m[11]*p[2] + m[15];
      if (w <= 1e-6) return { visible: false };
      const x = (m[0]*p[0] + m[4]*p[1] + m[8]*p[2] + m[12]) / w;
      const y = (m[1]*p[0] + m[5]*p[1] + m[9]*p[2] + m[13]) / w;
      const r = this.cv.getBoundingClientRect();
      return { visible: Math.abs(x) < 1.25 && Math.abs(y) < 1.25,
               x: (x * 0.5 + 0.5) * r.width, y: (0.5 - y * 0.5) * r.height };
    }
  }

  Scene3D.M = M;
  global.Scene3D = Scene3D;
})(window);
