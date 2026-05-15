const canvas = document.getElementById("pet-canvas");
const modeEl = document.getElementById("pet-mode");
const fallbackEl = document.getElementById("pet-fallback");
const panelEl = document.getElementById("pet-panel");
const viewportEl = document.getElementById("pet-viewport");
const dragHandleEl = document.getElementById("pet-drag-handle");
const openBtn = document.getElementById("btn-pet-open");
const collapseBtn = document.getElementById("btn-pet-collapse");
const closeBtn = document.getElementById("btn-pet-close");
const zoomSlider = document.getElementById("pet-zoom-slider");
const zoomValueEl = document.getElementById("pet-zoom-value");

const JOINTS = ["base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"];
const PANEL_STATE_KEY = "lampgo.petPanelState";
const ZOOM_STATE_KEY = "lampgo.petZoom";
const DEFAULT_ZOOM = 1.8;
const DEFAULT_RIG = {
  model: "/assets/pet/lampgoGLB.glb",
  draco: {
    decoderPath: "https://www.gstatic.com/draco/versioned/decoders/1.5.6/",
  },
  joints: {
    base_yaw: { node: "base_yaw", axis: "y", direction: 0.55, offset: 0 },
    base_pitch: { node: "base_pitch", axis: "z", direction: -0.42, offset: 0 },
    elbow_pitch: { node: "elbow_pitch", axis: "z", direction: 0.42, offset: 0 },
    wrist_roll: { node: "wrist_roll", axis: "y", direction: 0.55, offset: 0 },
    wrist_pitch: { node: "wrist_pitch", axis: "z", direction: -0.38, offset: 0 },
  },
};

let latestPose = Object.fromEntries(JOINTS.map((joint) => [joint, 0]));
let latestMeta = { mode: "idle" };
let rendererApi = null;
let panelState = loadPanelState();
let petZoom = loadPetZoom();

window.addEventListener("lampgo:pet-pose", (ev) => {
  const data = ev.detail || {};
  latestPose = { ...latestPose, ...(data.joint_positions || {}) };
  latestMeta = data;
  updateMode(data);
  if (rendererApi) rendererApi.setPose(latestPose, data);
});

window.addEventListener("lampgo:status", (ev) => {
  const data = ev.detail || {};
  updateMode(data);
});

init();

async function init() {
  if (!canvas) return;
  initPanelControls();
  initZoomControl();
  const rig = await loadRig();
  try {
    const THREE = await import("https://esm.sh/three@0.160.0");
    const loaderMod = await import("https://esm.sh/three@0.160.0/examples/jsm/loaders/GLTFLoader.js");
    const dracoMod = await import("https://esm.sh/three@0.160.0/examples/jsm/loaders/DRACOLoader.js");
    rendererApi = await initThree(THREE, loaderMod.GLTFLoader, dracoMod.DRACOLoader, rig);
  } catch (err) {
    console.warn("[pet] Three.js unavailable, using canvas fallback:", err);
    rendererApi = initCanvasFallback();
  }
  rendererApi.setPose(latestPose, latestMeta);
  rendererApi.setZoom && rendererApi.setZoom(petZoom);
}

async function loadRig() {
  try {
    const resp = await fetch("/assets/pet/rig.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rig = await resp.json();
    return {
      ...DEFAULT_RIG,
      ...rig,
      draco: { ...DEFAULT_RIG.draco, ...(rig.draco || {}) },
      joints: { ...DEFAULT_RIG.joints, ...(rig.joints || {}) },
    };
  } catch (err) {
    console.warn("[pet] rig.json unavailable, using default rig:", err);
    return DEFAULT_RIG;
  }
}

async function initThree(THREE, GLTFLoader, DRACOLoader, rig) {
  canvas.dataset.petRenderer = "three";
  canvas.dataset.petRig = "loading";
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.01, 100);
  camera.position.set(3.4, 2.4, 4.2);
  camera.lookAt(0, 1.2, 0);

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);

  const controls = createOrbitView(THREE, camera, viewportEl || canvas);
  controls.setZoom(petZoom);

  scene.add(new THREE.HemisphereLight(0xffffff, 0xd9c7ba, 2.8));
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(3, 5, 4);
  scene.add(key);

  const nodes = new Map();
  const root = buildFallbackModel(THREE, nodes);
  storeBaseRotation(root);
  scene.add(root);

  let displayRoot = root;
  let hasRigNodes = true;
  loadGlbIfPresent(THREE, GLTFLoader, DRACOLoader, rig, scene, root, nodes, camera, controls).then((loaded) => {
    if (loaded) {
      displayRoot = loaded.model;
      hasRigNodes = loaded.hasRigNodes;
    }
  });

  let targetPose = { ...latestPose };
  let shownPose = { ...latestPose };

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function applyPose() {
    let drivenNodes = 0;
    for (const [joint, conf] of Object.entries(rig.joints || {})) {
      const node = nodes.get(conf.node || joint);
      if (!node) continue;
      drivenNodes += 1;
      const value = Number(shownPose[joint] || 0);
      const angle = THREE.MathUtils.degToRad((value + Number(conf.offset || 0)) * Number(conf.direction || 1));
      const axis = conf.axis || "z";
      if (axis === "x") node.rotation.x = angle;
      else if (axis === "y") node.rotation.y = angle;
      else node.rotation.z = angle;
    }
    if (drivenNodes === 0 && displayRoot) {
      const idle = latestMeta.virtual_motion || latestMeta.no_hw ? Math.sin(performance.now() * 0.0012) : 0;
      const base = displayRoot.userData.petBaseRotation || { x: 0, y: 0, z: 0 };
      displayRoot.rotation.x = base.x + THREE.MathUtils.degToRad(Number(shownPose.base_pitch || 0) * 0.08);
      displayRoot.rotation.y = base.y + THREE.MathUtils.degToRad(Number(shownPose.base_yaw || 0) * 0.4 + idle * 1.4);
      displayRoot.rotation.z = base.z + THREE.MathUtils.degToRad((Number(shownPose.elbow_pitch || 0) + Number(shownPose.wrist_pitch || 0)) * 0.04 + idle * 0.6);
    }
  }

  function tick() {
    resize();
    for (const joint of JOINTS) {
      shownPose[joint] = lerp(Number(shownPose[joint] || 0), Number(targetPose[joint] || 0), 0.22);
    }
    controls.update();
    applyPose();
    canvas.dataset.petRig = hasRigNodes ? "rigged" : "whole-model";
    canvas.dataset.petTheta = controls.theta.toFixed(3);
    canvas.dataset.petRadius = controls.radius.toFixed(3);
    canvas.dataset.petZoom = controls.zoom.toFixed(2);
    canvas.dataset.petBasePitch = Number(shownPose.base_pitch || 0).toFixed(2);
    canvas.dataset.petElbowPitch = Number(shownPose.elbow_pitch || 0).toFixed(2);
    canvas.dataset.petWristPitch = Number(shownPose.wrist_pitch || 0).toFixed(2);
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }

  tick();

  return {
    setPose(pose) {
      targetPose = { ...targetPose, ...pose };
    },
    resetView() {
      fitModelToView(THREE, displayRoot, camera, controls);
    },
    setZoom(value) {
      controls.setZoom(value);
    },
    hasRigNodes() {
      return hasRigNodes;
    },
  };
}

function buildFallbackModel(THREE, nodes) {
  const root = new THREE.Group();
  root.name = "lampgo_pet_fallback";

  const white = new THREE.MeshStandardMaterial({ color: 0xf3f1ec, roughness: 0.42, metalness: 0.08 });
  const whiteSoft = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, metalness: 0.04 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x2b2d31, roughness: 0.34, metalness: 0.12 });
  const darkSoft = new THREE.MeshStandardMaterial({ color: 0x4c4d4f, roughness: 0.45, metalness: 0.08 });
  const orange = new THREE.MeshStandardMaterial({ color: 0xe37b3b, roughness: 0.44, metalness: 0.02 });
  const face = new THREE.MeshStandardMaterial({ color: 0x171719, roughness: 0.28, metalness: 0.08 });
  const led = new THREE.MeshStandardMaterial({ color: 0xf8f7f2, roughness: 0.35, metalness: 0.0 });

  const base = roundedBox(THREE, 1.9, 0.22, 1.06, 0.18, white);
  base.position.y = 0.12;
  root.add(base);

  const baseFoot = roundedBox(THREE, 1.55, 0.05, 0.86, 0.08, darkSoft);
  baseFoot.position.y = -0.005;
  root.add(baseFoot);

  const yaw = new THREE.Group();
  yaw.name = "base_yaw";
  yaw.position.set(-0.32, 0.25, 0);
  nodes.set("base_yaw", yaw);
  root.add(yaw);

  const pedestal = roundedBox(THREE, 0.38, 0.58, 0.42, 0.08, dark);
  pedestal.position.y = 0.25;
  yaw.add(pedestal);

  const yawCap = new THREE.Mesh(new THREE.CylinderGeometry(0.19, 0.19, 0.05, 36), whiteSoft);
  yawCap.rotation.x = Math.PI / 2;
  yawCap.position.set(-0.23, 0.34, 0.24);
  yaw.add(yawCap);

  const lowerCableA = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.56, 14), orange);
  lowerCableA.position.set(0.10, 0.26, 0.24);
  lowerCableA.rotation.z = -0.18;
  yaw.add(lowerCableA);
  const lowerCableB = lowerCableA.clone();
  lowerCableB.position.x = 0.17;
  yaw.add(lowerCableB);

  const pitch = new THREE.Group();
  pitch.name = "base_pitch";
  pitch.position.set(0.03, 0.54, 0);
  nodes.set("base_pitch", pitch);
  yaw.add(pitch);

  const lower = roundedBox(THREE, 0.24, 1.35, 0.26, 0.11, white);
  lower.position.y = 0.68;
  pitch.add(lower);

  const lowerSideCap = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.14, 0.04, 36), whiteSoft);
  lowerSideCap.rotation.x = Math.PI / 2;
  lowerSideCap.position.set(-0.16, 0.08, 0.17);
  pitch.add(lowerSideCap);

  const cable = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 1.16, 14), orange);
  cable.position.set(0.16, 0.66, 0.16);
  pitch.add(cable);

  const elbow = new THREE.Group();
  elbow.name = "elbow_pitch";
  elbow.position.set(0, 1.33, 0);
  nodes.set("elbow_pitch", elbow);
  pitch.add(elbow);

  const elbowJoint = roundedBox(THREE, 0.46, 0.34, 0.46, 0.11, dark);
  elbow.add(elbowJoint);

  const elbowCap = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.045, 36), whiteSoft);
  elbowCap.rotation.x = Math.PI / 2;
  elbowCap.position.set(-0.22, 0.0, 0.24);
  elbow.add(elbowCap);

  const upper = roundedBox(THREE, 0.26, 1.42, 0.26, 0.12, white);
  upper.position.y = 0.72;
  elbow.add(upper);

  const upperCable = new THREE.Mesh(new THREE.CylinderGeometry(0.024, 0.024, 1.18, 14), orange);
  upperCable.position.set(0.17, 0.63, 0.14);
  elbow.add(upperCable);

  const wristRoll = new THREE.Group();
  wristRoll.name = "wrist_roll";
  wristRoll.position.set(0, 1.43, 0);
  nodes.set("wrist_roll", wristRoll);
  elbow.add(wristRoll);

  const wristPitch = new THREE.Group();
  wristPitch.name = "wrist_pitch";
  wristPitch.position.set(0, 0.02, 0);
  nodes.set("wrist_pitch", wristPitch);
  wristRoll.add(wristPitch);

  const wristHousing = roundedBox(THREE, 0.46, 0.36, 0.46, 0.11, dark);
  wristPitch.add(wristHousing);

  const head = roundedBox(THREE, 1.28, 0.46, 0.44, 0.20, white);
  head.position.set(0.62, 0.03, 0);
  wristPitch.add(head);

  const facePlate = roundedBox(THREE, 0.92, 0.27, 0.035, 0.11, face);
  facePlate.position.set(0.62, 0.03, 0.235);
  wristPitch.add(facePlate);

  for (let row = 0; row < 5; row += 1) {
    for (let col = 0; col < 12; col += 1) {
      const dot = new THREE.Mesh(new THREE.BoxGeometry(0.035, 0.035, 0.012), led);
      dot.position.set(0.22 + col * 0.065, -0.09 + row * 0.045, 0.258);
      wristPitch.add(dot);
    }
  }

  const brow = roundedBox(THREE, 0.27, 0.07, 0.018, 0.025, orange);
  brow.position.set(0.28, 0.16, 0.275);
  wristPitch.add(brow);

  root.scale.setScalar(0.92);
  root.position.set(0.03, -0.12, 0);

  return root;
}

function roundedBox(THREE, width, height, depth, radius, material) {
  const shape = new THREE.Shape();
  const x = -width / 2;
  const y = -height / 2;
  const r = Math.min(radius, width / 2, height / 2);
  shape.moveTo(x + r, y);
  shape.lineTo(x + width - r, y);
  shape.quadraticCurveTo(x + width, y, x + width, y + r);
  shape.lineTo(x + width, y + height - r);
  shape.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  shape.lineTo(x + r, y + height);
  shape.quadraticCurveTo(x, y + height, x, y + height - r);
  shape.lineTo(x, y + r);
  shape.quadraticCurveTo(x, y, x + r, y);
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth,
    bevelEnabled: true,
    bevelSize: Math.min(r * 0.35, depth * 0.35),
    bevelThickness: Math.min(r * 0.35, depth * 0.35),
    bevelSegments: 8,
  });
  geometry.center();
  return new THREE.Mesh(geometry, material);
}

async function loadGlbIfPresent(THREE, GLTFLoader, DRACOLoader, rig, scene, fallbackRoot, nodes, camera, controls) {
  if (!rig.model) return null;
  let dracoLoader = null;
  try {
    const loader = new GLTFLoader();
    if (DRACOLoader && rig.draco !== false) {
      dracoLoader = new DRACOLoader();
      dracoLoader.setDecoderPath((rig.draco && rig.draco.decoderPath) || DEFAULT_RIG.draco.decoderPath);
      loader.setDRACOLoader(dracoLoader);
    }
    const gltf = await loader.loadAsync(rig.model);
    scene.remove(fallbackRoot);
    nodes.clear();
    const lookup = collectModelNodes(gltf.scene);
    gltf.scene.traverse((obj) => {
      if (obj.name) nodes.set(obj.name, obj);
      if (obj.isMesh) {
        obj.castShadow = true;
        obj.frustumCulled = false;
      }
    });
    const linkRig = buildLinkRig(THREE, gltf.scene, lookup, nodes, rig);
    normalizeModel(THREE, gltf.scene);
    storeBaseRotation(gltf.scene);
    scene.add(gltf.scene);
    fitModelToView(THREE, gltf.scene, camera, controls);
    fallbackEl && fallbackEl.classList.add("hidden");
    const hasRigNodes = !!linkRig || Object.values(rig.joints || {}).some((conf) => nodes.has(conf.node));
    canvas.dataset.petRig = hasRigNodes ? "rigged" : "whole-model";
    canvas.dataset.petMissing = linkRig && linkRig.missing.length ? linkRig.missing.join(",") : "";
    canvas.dataset.petLinks = linkRig ? String(linkRig.groups.size) : "0";
    console.info("[pet] GLB loaded", {
      model: rig.model,
      nodes: lookup.all.length,
      hasRigNodes,
      linkRig: !!linkRig,
      missing: linkRig ? linkRig.missing : [],
    });
    return { model: gltf.scene, hasRigNodes };
  } catch (err) {
    console.warn("[pet] GLB unavailable, using generated fallback model:", err);
    return null;
  } finally {
    if (dracoLoader) dracoLoader.dispose();
  }
}

function collectModelNodes(model) {
  const all = [];
  const byName = new Map();
  model.traverse((obj) => {
    if (!obj.name) return;
    const list = byName.get(obj.name) || [];
    list.push(obj);
    byName.set(obj.name, list);
    all.push(obj);
  });
  return { all, byName };
}

function buildLinkRig(THREE, model, lookup, jointNodes, rig) {
  const links = (rig.linkRig && Array.isArray(rig.linkRig.links)) ? rig.linkRig.links : [];
  if (!links.length) return null;

  const groups = new Map();
  const missing = [];
  let parent = model;
  let parentPivot = new THREE.Vector3(0, 0, 0);

  model.updateMatrixWorld(true);

  for (const link of links) {
    const pivot = vectorFromArray(THREE, link.pivot || [0, 0, 0]);
    const group = new THREE.Group();
    group.name = link.joint;
    group.position.copy(pivot).sub(parentPivot);
    group.userData.petBaseRotation = { x: group.rotation.x, y: group.rotation.y, z: group.rotation.z };
    parent.add(group);
    groups.set(link.joint, group);
    jointNodes.set(link.joint, group);
    parent = group;
    parentPivot = pivot;
  }

  model.updateMatrixWorld(true);

  for (const link of links) {
    const group = groups.get(link.joint);
    if (!group) continue;
    for (const selector of link.attach || []) {
      const targets = resolveNodeSelector(lookup, selector);
      if (!targets.length) {
        missing.push(selector);
        continue;
      }
      for (const target of targets) {
        if (target === group || isAncestor(target, group)) continue;
        group.attach(target);
      }
    }
  }

  model.updateMatrixWorld(true);
  return { groups, missing };
}

function resolveNodeSelector(lookup, selector) {
  if (!selector) return [];
  if (selector === "*") return lookup.all;
  const [name, occurrenceText] = String(selector).split("#");
  const matches = nodesMatchingName(lookup, name);
  if (occurrenceText === undefined || occurrenceText === "*") return matches;
  const occurrence = Number(occurrenceText);
  if (!Number.isInteger(occurrence) || occurrence < 0) return [];
  return matches[occurrence] ? [matches[occurrence]] : [];
}

function nodesMatchingName(lookup, name) {
  const out = [];
  const seen = new Set();
  for (const obj of lookup.all) {
    if (obj.name === name || obj.name.startsWith(`${name}_`)) {
      if (seen.has(obj)) continue;
      seen.add(obj);
      out.push(obj);
    }
  }
  return out;
}

function isAncestor(candidate, obj) {
  let current = obj.parent;
  while (current) {
    if (current === candidate) return true;
    current = current.parent;
  }
  return false;
}

function vectorFromArray(THREE, value) {
  return new THREE.Vector3(Number(value[0] || 0), Number(value[1] || 0), Number(value[2] || 0));
}

function normalizeModel(THREE, model) {
  const box = new THREE.Box3().setFromObject(model);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  model.position.sub(center);
  const maxDim = Math.max(size.x, size.y, size.z, 0.001);
  model.scale.multiplyScalar(2.55 / maxDim);
  const fitBox = new THREE.Box3().setFromObject(model);
  const minY = fitBox.min.y;
  model.position.y -= minY - 0.05;
  model.rotation.y = Math.PI * -0.18;
}

function storeBaseRotation(model) {
  model.userData.petBaseRotation = {
    x: model.rotation.x,
    y: model.rotation.y,
    z: model.rotation.z,
  };
}

function fitModelToView(THREE, model, camera, controls) {
  const box = new THREE.Box3().setFromObject(model);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z, 0.1) * 0.72;
  controls.target.copy(center);
  camera.position.set(center.x + radius * 1.4, center.y + radius * 0.9, center.z + radius * 1.8);
  camera.near = Math.max(radius / 100, 0.01);
  camera.far = Math.max(radius * 20, 20);
  camera.updateProjectionMatrix();
  if (controls.syncFromCamera) controls.syncFromCamera();
  if (controls.setZoom) controls.setZoom(petZoom);
  controls.update();
}

function createOrbitView(THREE, camera, domElement) {
  const controls = {
    target: new THREE.Vector3(0, 1.15, 0),
    radius: 4.2,
    theta: 0,
    phi: Math.PI * 0.36,
    minDistance: 0.7,
    maxDistance: 8.0,
    zoom: petZoom,
    dragging: null,
    syncFromCamera,
    setZoom,
    update,
  };

  function syncFromCamera() {
    const offset = camera.position.clone().sub(controls.target);
    controls.radius = clamp(offset.length(), controls.minDistance, controls.maxDistance);
    controls.theta = Math.atan2(offset.x, offset.z);
    controls.phi = Math.acos(clamp(offset.y / Math.max(controls.radius, 0.001), -0.98, 0.98));
  }

  function update() {
    const effectiveRadius = controls.radius / Math.max(controls.zoom, 0.1);
    const sinPhiRadius = Math.sin(controls.phi) * effectiveRadius;
    camera.position.set(
      controls.target.x + sinPhiRadius * Math.sin(controls.theta),
      controls.target.y + Math.cos(controls.phi) * effectiveRadius,
      controls.target.z + sinPhiRadius * Math.cos(controls.theta),
    );
    camera.lookAt(controls.target);
  }

  function setZoom(value) {
    controls.zoom = clamp(Number(value) || DEFAULT_ZOOM, 0.8, 2.8);
  }

  function beginDrag(id, x, y) {
    controls.dragging = { pointerId: id, x, y };
  }

  function moveDrag(id, x, y) {
    if (!controls.dragging || controls.dragging.pointerId !== id) return false;
    const dx = x - controls.dragging.x;
    const dy = y - controls.dragging.y;
    controls.dragging.x = x;
    controls.dragging.y = y;
    controls.theta -= dx * 0.008;
    controls.phi = clamp(controls.phi - dy * 0.008, 0.18, Math.PI - 0.18);
    return true;
  }

  function endDrag(id) {
    if (!controls.dragging || controls.dragging.pointerId !== id) return false;
    controls.dragging = null;
    return true;
  }

  domElement.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    beginDrag(ev.pointerId, ev.clientX, ev.clientY);
    domElement.setPointerCapture(ev.pointerId);
    ev.preventDefault();
  });

  domElement.addEventListener("pointermove", (ev) => {
    if (moveDrag(ev.pointerId, ev.clientX, ev.clientY)) ev.preventDefault();
  });

  const stopDrag = (ev) => {
    if (endDrag(ev.pointerId)) ev.preventDefault();
  };
  domElement.addEventListener("pointerup", stopDrag);
  domElement.addEventListener("pointercancel", stopDrag);

  domElement.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    beginDrag("mouse", ev.clientX, ev.clientY);
    ev.preventDefault();
  });

  window.addEventListener("mousemove", (ev) => {
    if (moveDrag("mouse", ev.clientX, ev.clientY)) ev.preventDefault();
  });

  window.addEventListener("mouseup", (ev) => {
    if (endDrag("mouse")) ev.preventDefault();
  });

  domElement.addEventListener("wheel", (ev) => {
    controls.radius = clamp(controls.radius * Math.exp(ev.deltaY * 0.001), controls.minDistance, controls.maxDistance);
    ev.preventDefault();
  }, { passive: false });

  syncFromCamera();
  update();
  return controls;
}

function initCanvasFallback() {
  canvas.dataset.petRenderer = "canvas";
  const ctx = canvas.getContext("2d");
  let targetPose = { ...latestPose };
  let shownPose = { ...latestPose };
  let zoom = petZoom;

  function draw() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.floor(rect.width * ratio));
    const height = Math.max(1, Math.floor(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.scale(ratio, ratio);
    const w = width / ratio;
    const h = height / ratio;
    for (const joint of JOINTS) {
      shownPose[joint] = lerp(Number(shownPose[joint] || 0), Number(targetPose[joint] || 0), 0.2);
    }

    const visualZoom = clamp(zoom, 0.8, 2.8);
    const baseX = w * 0.48 + shownPose.base_yaw * 0.08;
    const baseY = h * (0.74 + (1 / visualZoom) * 0.04);
    const l1 = h * 0.27 * visualZoom;
    const l2 = h * 0.30 * visualZoom;
    const a1 = degToRad(-110 - shownPose.base_pitch * 0.45);
    const a2 = a1 + degToRad(76 + shownPose.elbow_pitch * 0.45);
    const p1 = [baseX + Math.cos(a1) * l1, baseY + Math.sin(a1) * l1];
    const p2 = [p1[0] + Math.cos(a2) * l2, p1[1] + Math.sin(a2) * l2];

    ctx.fillStyle = "rgba(80, 62, 48, 0.12)";
    ctx.beginPath();
    ctx.ellipse(baseX, baseY + 25, 76, 14, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#f4f1ec";
    roundRect(ctx, baseX - 74, baseY + 4, 148, 32, 16);
    ctx.fill();
    ctx.fillStyle = "#4c4d4f";
    roundRect(ctx, baseX - 52, baseY + 28, 104, 7, 4);
    ctx.fill();

    ctx.fillStyle = "#303236";
    roundRect(ctx, baseX - 18, baseY - 44, 36, 50, 8);
    ctx.fill();

    ctx.lineCap = "round";
    ctx.lineWidth = 18;
    ctx.strokeStyle = "#f4eee8";
    ctx.beginPath();
    ctx.moveTo(baseX, baseY);
    ctx.lineTo(p1[0], p1[1]);
    ctx.lineTo(p2[0], p2[1]);
    ctx.stroke();

    ctx.lineWidth = 4;
    ctx.strokeStyle = "#e57d3f";
    ctx.beginPath();
    ctx.moveTo(baseX + 10, baseY - 28);
    ctx.lineTo(p1[0] + 10, p1[1] + 2);
    ctx.lineTo(p2[0] + 8, p2[1] + 2);
    ctx.stroke();

    for (const p of [[baseX, baseY], p1, p2]) {
      ctx.fillStyle = "#303236";
      ctx.beginPath();
      ctx.arc(p[0], p[1], 15, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.save();
    ctx.translate(p2[0], p2[1]);
    ctx.rotate(a2 + degToRad(-32 - shownPose.wrist_pitch * 0.35));
    ctx.fillStyle = "#f4eee8";
    roundRect(ctx, -15, -22, 92, 38, 18);
    ctx.fill();
    ctx.fillStyle = "#17191c";
    roundRect(ctx, -4, -10, 62, 18, 8);
    ctx.fill();
    ctx.fillStyle = "#e57d3f";
    ctx.fillRect(4, -4, 10, 2);
    ctx.fillStyle = "#f7f5ef";
    for (let row = 0; row < 3; row += 1) {
      for (let col = 0; col < 9; col += 1) {
        ctx.fillRect(18 + col * 4.2, -5 + row * 4.2, 2.4, 2.4);
      }
    }
    ctx.restore();
    ctx.restore();
    requestAnimationFrame(draw);
  }

  fallbackEl && fallbackEl.classList.add("hidden");
  requestAnimationFrame(draw);
  return {
    setPose(pose) {
      targetPose = { ...targetPose, ...pose };
    },
    setZoom(value) {
      zoom = clamp(Number(value) || DEFAULT_ZOOM, 0.8, 2.8);
      canvas.dataset.petZoom = zoom.toFixed(2);
    },
  };
}

function initZoomControl() {
  if (!zoomSlider) return;
  zoomSlider.value = String(Math.round(petZoom * 100));
  updateZoomValue();
  zoomSlider.addEventListener("input", () => {
    petZoom = clamp(Number(zoomSlider.value) / 100, 0.8, 2.8);
    updateZoomValue();
    rendererApi && rendererApi.setZoom && rendererApi.setZoom(petZoom);
    savePetZoom();
  });
}

function updateZoomValue() {
  if (zoomValueEl) zoomValueEl.textContent = `${Math.round(petZoom * 100)}%`;
}

function initPanelControls() {
  if (!panelEl) return;
  applyPanelState();

  openBtn && openBtn.addEventListener("click", () => {
    panelState.hidden = false;
    panelState.collapsed = false;
    savePanelState();
    applyPanelState();
  });

  closeBtn && closeBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    panelState.hidden = true;
    savePanelState();
    applyPanelState();
  });

  collapseBtn && collapseBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    panelState.collapsed = !panelState.collapsed;
    savePanelState();
    applyPanelState();
    setTimeout(() => rendererApi && rendererApi.resetView && rendererApi.resetView(), 220);
  });

  let dragging = null;
  dragHandleEl && dragHandleEl.addEventListener("pointerdown", (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest("button")) return;
    const rect = panelEl.getBoundingClientRect();
    dragging = {
      pointerId: ev.pointerId,
      dx: ev.clientX - rect.left,
      dy: ev.clientY - rect.top,
    };
    panelEl.classList.add("is-dragging");
    dragHandleEl.setPointerCapture(ev.pointerId);
  });

  dragHandleEl && dragHandleEl.addEventListener("pointermove", (ev) => {
    if (!dragging || ev.pointerId !== dragging.pointerId) return;
    const rect = panelEl.getBoundingClientRect();
    const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
    const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
    panelState.left = clamp(ev.clientX - dragging.dx, 8, maxLeft);
    panelState.top = clamp(ev.clientY - dragging.dy, 8, maxTop);
    panelState.placed = true;
    applyPanelState(false);
  });

  const finishDrag = (ev) => {
    if (!dragging || ev.pointerId !== dragging.pointerId) return;
    dragging = null;
    panelEl.classList.remove("is-dragging");
    savePanelState();
  };
  dragHandleEl && dragHandleEl.addEventListener("pointerup", finishDrag);
  dragHandleEl && dragHandleEl.addEventListener("pointercancel", finishDrag);

  window.addEventListener("resize", () => {
    if (!panelState.placed) return;
    const rect = panelEl.getBoundingClientRect();
    panelState.left = clamp(panelState.left || rect.left, 8, Math.max(8, window.innerWidth - rect.width - 8));
    panelState.top = clamp(panelState.top || rect.top, 8, Math.max(8, window.innerHeight - rect.height - 8));
    applyPanelState(false);
    savePanelState();
  });
}

function applyPanelState(animate = true) {
  if (!panelEl) return;
  panelEl.classList.toggle("hidden", !!panelState.hidden);
  panelEl.classList.toggle("is-collapsed", !!panelState.collapsed);
  if (collapseBtn) collapseBtn.textContent = panelState.collapsed ? "+" : "−";
  if (openBtn) openBtn.classList.toggle("hidden", !panelState.hidden);
  if (panelState.placed && Number.isFinite(panelState.left) && Number.isFinite(panelState.top)) {
    panelEl.style.left = `${panelState.left}px`;
    panelEl.style.top = `${panelState.top}px`;
    panelEl.style.right = "auto";
    panelEl.style.bottom = "auto";
  }
  if (!animate) {
    panelEl.style.transition = "none";
    requestAnimationFrame(() => {
      panelEl.style.transition = "";
    });
  }
}

function loadPanelState() {
  try {
    const raw = localStorage.getItem(PANEL_STATE_KEY);
    if (!raw) return { hidden: false, collapsed: false, placed: false };
    const parsed = JSON.parse(raw);
    return {
      hidden: !!parsed.hidden,
      collapsed: !!parsed.collapsed,
      placed: !!parsed.placed,
      left: Number(parsed.left),
      top: Number(parsed.top),
    };
  } catch {
    return { hidden: false, collapsed: false, placed: false };
  }
}

function savePanelState() {
  try {
    localStorage.setItem(PANEL_STATE_KEY, JSON.stringify(panelState));
  } catch {
    // Ignore private browsing / storage-denied cases.
  }
}

function loadPetZoom() {
  try {
    const value = Number(localStorage.getItem(ZOOM_STATE_KEY));
    if (Number.isFinite(value)) return clamp(value, 0.8, 2.8);
  } catch {
    // Ignore private browsing / storage-denied cases.
  }
  return DEFAULT_ZOOM;
}

function savePetZoom() {
  try {
    localStorage.setItem(ZOOM_STATE_KEY, String(petZoom));
  } catch {
    // Ignore private browsing / storage-denied cases.
  }
}

function updateMode(data) {
  if (!modeEl || !data) return;
  if (data.virtual_motion || data.mode === "virtual" || data.no_hw) modeEl.textContent = "虚拟";
  else if (data.hal_connected || data.mode === "hardware") modeEl.textContent = "硬件";
  else modeEl.textContent = "待机";
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function degToRad(v) {
  return (Number(v) || 0) * Math.PI / 180;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
