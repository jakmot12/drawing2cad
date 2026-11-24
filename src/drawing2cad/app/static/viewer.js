// Interactive 3D model preview (three.js, bundled locally): STL triangles + B-Rep edges.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import { STLLoader } from "/static/vendor/three/STLLoader.js";

const VIEWS = {
  iso: [1, -1.15, 0.85],
  front: [0, -1, 0],   // looking along +Y (European first-angle front view)
  top: [0, 0, 1],
  right: [1, 0, 0],
};

export class ModelViewer {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100000);
    this.camera.up.set(0, 0, 1); // CAD convention: Z up

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.autoRotateSpeed = 1.6;

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445066, 1.6));
    this.keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    this.fillLight = new THREE.DirectionalLight(0xbfd4ff, 0.8);
    this.scene.add(this.keyLight, this.fillLight);

    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.edgesVisible = true;
    this.radius = 50;
    this.center = new THREE.Vector3();

    this._resize = this._resize.bind(this);
    this._ro = new ResizeObserver(this._resize);
    this._ro.observe(container);
    this._resize();
    this._running = true;
    const loop = () => {
      if (!this._running) return;
      this.controls.update();
      this.keyLight.position.copy(this.camera.position).add(new THREE.Vector3(this.radius, -this.radius, this.radius * 2));
      this.fillLight.position.copy(this.center).sub(this.camera.position).add(this.center);
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  _resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _materials(rejected) {
    const base = rejected ? 0xd9776f : (document.documentElement.dataset.theme === "light" ? 0xa9b7c9 : 0x8fa3bd);
    return {
      mesh: new THREE.MeshStandardMaterial({
        color: base, metalness: 0.35, roughness: 0.42,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      }),
      edges: new THREE.LineBasicMaterial({
        color: document.documentElement.dataset.theme === "light" ? 0x1d2735 : 0x0c1118,
        transparent: true, opacity: 0.9,
      }),
    };
  }

  clear() {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      child.geometry?.dispose();
      child.material?.dispose();
    }
  }

  async load(stlUrl, edgesUrl, { rejected = false } = {}) {
    this.clear();
    const [geometry, edgeData] = await Promise.all([
      new STLLoader().loadAsync(stlUrl),
      fetch(edgesUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    const mats = this._materials(rejected);
    geometry.computeVertexNormals();
    this.mesh = new THREE.Mesh(geometry, mats.mesh);
    this.group.add(this.mesh);

    let edgeGeom;
    if (edgeData && edgeData.edges) {
      const pos = [];
      for (const pl of edgeData.edges) {
        for (let i = 0; i + 1 < pl.length; i++) pos.push(...pl[i], ...pl[i + 1]);
      }
      edgeGeom = new THREE.BufferGeometry();
      edgeGeom.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    } else {
      edgeGeom = new THREE.EdgesGeometry(geometry, 28);
    }
    this.edges = new THREE.LineSegments(edgeGeom, mats.edges);
    this.edges.visible = this.edgesVisible;
    this.group.add(this.edges);

    geometry.computeBoundingBox();
    const box = geometry.boundingBox;
    box.getCenter(this.center);
    const size = new THREE.Vector3();
    box.getSize(size);
    this.size = size;
    this.radius = Math.max(size.length() / 2, 1);
    this.controls.target.copy(this.center);
    this.setView("iso");
  }

  setView(name) {
    const d = new THREE.Vector3(...(VIEWS[name] || VIEWS.iso)).normalize();
    const fov = (this.camera.fov * Math.PI) / 180;
    const fit = Math.max(1, 1 / Math.min(1, this.camera.aspect));
    const dist = (this.radius / Math.sin(fov / 2)) * 1.0 * fit;
    this.camera.position.copy(this.center).addScaledVector(d, dist);
    this.camera.up.set(0, 0, 1);
    if (name === "top") this.camera.up.set(0, 1, 0);
    this.camera.near = dist / 100;
    this.camera.far = dist * 100;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(this.center);
    this.controls.update();
  }

  setEdges(on) {
    this.edgesVisible = on;
    if (this.edges) this.edges.visible = on;
  }

  setAutoRotate(on) {
    this.controls.autoRotate = on;
  }

  refreshTheme(rejected) {
    if (!this.mesh) return;
    const mats = this._materials(rejected);
    this.mesh.material.dispose();
    this.mesh.material = mats.mesh;
    this.edges.material.dispose();
    this.edges.material = mats.edges;
  }

  dispose() {
    this._running = false;
    this._ro.disconnect();
    this.clear();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
