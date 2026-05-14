const canvas = document.getElementById("pet-canvas");
const modeEl = document.getElementById("pet-mode");
const fallbackEl = document.getElementById("pet-fallback");

const JOINTS = ["base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"];
const DEFAULT_RIG = {
  model: "/assets/pet/lampgo.glb",
  joints: {
    base_yaw: { node: "base_yaw", axis: "y", direction: 1, offset: 0 },
    base_pitch: { node: "base_pitch", axis: "z", direction: -0.55, offset: -22 },
    elbow_pitch: { node: "elbow_pitch", axis: "z", direction: 0.55, offset: 62 },
    wrist_roll: { node: "wrist_roll", axis: "y", direction: 1, offset: 0 },
    wrist_pitch: { node: "wrist_pitch", axis: "z", direction: -0.45, offset: -34 },
  },
};

let latestPose = Object.fromEntries(JOINTS.map((joint) => [joint, 0]));
let latestMeta = { mode: "idle" };
let rendererApi = null;

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
  const rig = await loadRig();
  try {
    const THREE = await import("https://unpkg.com/three@0.160.0/build/three.module.js");
    const loaderMod = await import("https://unpkg.com/three@0.160.0/examples/jsm/loaders/GLTFLoader.js");
    rendererApi = await initThree(THREE, loaderMod.GLTFLoader, rig);
  } catch (err) {
    console.warn("[pet] Three.js unavailable, using canvas fallback:", err);
    rendererApi = initCanvasFallback();
  }
  rendererApi.setPose(latestPose, latestMeta);
}

async function loadRig() {
  try {
    const resp = await fetch("/assets/pet/rig.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rig = await resp.json();
    return { ...DEFAULT_RIG, ...rig, joints: { ...DEFAULT_RIG.joints, ...(rig.joints || {}) } };
  } catch (err) {
    console.warn("[pet] rig.json unavailable, using default rig:", err);
    return DEFAULT_RIG;
  }
}

async function initThree(THREE, GLTFLoader, rig) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  camera.position.set(4.4, 3.2, 5.8);
  camera.lookAt(0, 1.45, 0);

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  scene.add(new THREE.HemisphereLight(0xffffff, 0xd9c7ba, 2.8));
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(3, 5, 4);
  scene.add(key);

  const nodes = new Map();
  const root = buildFallbackModel(THREE, nodes);
  scene.add(root);

  loadGlbIfPresent(THREE, GLTFLoader, rig, scene, root, nodes);

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
    for (const [joint, conf] of Object.entries(rig.joints || {})) {
      const node = nodes.get(conf.node || joint);
      if (!node) continue;
      const value = Number(shownPose[joint] || 0);
      const angle = THREE.MathUtils.degToRad((value + Number(conf.offset || 0)) * Number(conf.direction || 1));
      const axis = conf.axis || "z";
      if (axis === "x") node.rotation.x = angle;
      else if (axis === "y") node.rotation.y = angle;
      else node.rotation.z = angle;
    }
  }

  function tick() {
    resize();
    for (const joint of JOINTS) {
      shownPose[joint] = lerp(Number(shownPose[joint] || 0), Number(targetPose[joint] || 0), 0.22);
    }
    root.rotation.y += 0.002;
    applyPose();
    renderer.render(scene, camera);
  }

  renderer.setAnimationLoop(tick);

  return {
    setPose(pose) {
      targetPose = { ...targetPose, ...pose };
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

async function loadGlbIfPresent(THREE, GLTFLoader, rig, scene, fallbackRoot, nodes) {
  if (!rig.model) return;
  try {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(rig.model);
    scene.remove(fallbackRoot);
    gltf.scene.traverse((obj) => {
      if (obj.name) nodes.set(obj.name, obj);
      if (obj.isMesh) {
        obj.castShadow = true;
        obj.frustumCulled = false;
      }
    });
    scene.add(gltf.scene);
  } catch (err) {
    console.warn("[pet] GLB unavailable, using generated fallback model:", err);
  }
}

function initCanvasFallback() {
  const ctx = canvas.getContext("2d");
  let targetPose = { ...latestPose };
  let shownPose = { ...latestPose };

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

    const baseX = w * 0.48 + shownPose.base_yaw * 0.08;
    const baseY = h * 0.80;
    const l1 = h * 0.27;
    const l2 = h * 0.30;
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
  };
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
