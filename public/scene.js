import * as THREE from './assets/three.module.js';
const canvas = document.querySelector('#coffee-scene');
try {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x161719, .03);
  const camera = new THREE.PerspectiveCamera(43, 1, .1, 100);
  camera.position.set(0, 5.8, 20);
  camera.lookAt(0, 4.4, 0);
  scene.add(new THREE.AmbientLight(0xc7c6c3, 1.65));
  const key = new THREE.DirectionalLight(0xffe5c2, 3.3);
  key.position.set(-6, 10, 8); scene.add(key);
  const rim = new THREE.DirectionalLight(0xd9e0e8, 2.2);
  rim.position.set(7, 4, -5); scene.add(rim);
  // A slightly asymmetric oval with a deep, irregular natural split.
  const geometry = new THREE.SphereGeometry(1, 48, 36);
  const vertices = geometry.attributes.position;
  const colors = [];
  for (let i = 0; i < vertices.count; i++) {
    const x = vertices.getX(i), y = vertices.getY(i), z = vertices.getZ(i);
    const creaseX = .075 * Math.sin(y * 3.6) + .014 * Math.sin(y * 19);
    const distance = Math.abs(x - creaseX);
    const front = THREE.MathUtils.smoothstep(z, .03, .35);
    const crease = Math.exp(-Math.pow(distance / .08, 2)) * front;
    const ends = Math.pow(Math.max(0, 1 - y * y), .55);
    const ripple = .006 * Math.sin(y * 31 + x * 17) * Math.cos(z * 23);
    const asymmetry = 1 + .045 * Math.sin(y * 2.5 + x * 2);
    vertices.setXYZ(i,
      x * .66 * asymmetry + ripple,
      y * .91,
      z * .44 - .34 * crease * ends + ripple
    );
    const shade = 1 - crease * .68;
    colors.push(shade, shade * .96, shade * .91);
  }
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  // Layered mottling, fine pores, and narrow wrinkles break up the surface.
  const textureCanvas = document.createElement('canvas');
  textureCanvas.width = textureCanvas.height = 256;
  const ctx = textureCanvas.getContext('2d');
  ctx.fillStyle = '#9c8b78'; ctx.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 1800; i++) {
    const x = Math.random() * 256, y = Math.random() * 256;
    const radius = 1 + Math.random() * 9;
    const spot = ctx.createRadialGradient(x, y, 0, x, y, radius);
    spot.addColorStop(0, Math.random() > .5 ? '#e4c99a24' : '#211b1738');
    spot.addColorStop(1, '#00000000');
    ctx.fillStyle = spot; ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  }
  for (let i = 0; i < 1600; i++) {
    const x = Math.random() * 256, y = Math.random() * 256;
    ctx.strokeStyle = i % 2 ? '#211b1733' : '#ebd2b11c';
    ctx.lineWidth = .4 + Math.random() * .6;
    ctx.beginPath(); ctx.moveTo(x, y);
    ctx.quadraticCurveTo(x - 1, y + 2, x + Math.random() * 2 - 1, y + 3 + Math.random() * 8);
    ctx.stroke();
  }
  const texture = new THREE.CanvasTexture(textureCanvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.MeshStandardMaterial({
    color: 0x655346, map: texture, vertexColors: true,
    roughness: .67, metalness: 0, bumpMap: texture, bumpScale: .045
  });
  const beans = [];
  for (let i = 0; i < 20; i++) {
    const mesh = new THREE.Mesh(geometry, material);
    const scale = .46 + Math.random() * .38;
    mesh.scale.setScalar(scale);
    const rotation = new THREE.Euler(
      (Math.random() - .5) * 1.2,
      (Math.random() - .5) * 1.7,
      (Math.random() - .5) * 3
    );
    mesh.rotation.copy(rotation);
    scene.add(mesh);
    beans.push({
      mesh, rotation, anchor: new THREE.Vector3(),
      depth: -3 + Math.random() * 5,
      phase: Math.random() * Math.PI * 2,
      speed: .28 + Math.random() * .16,
      jitterX: (Math.random() - .5) * .10,
      jitterY: (Math.random() - .5) * .10
    });
  }
  let paused = document.querySelector('#motion-toggle').getAttribute('aria-pressed') === 'true';
  let visible = !document.hidden;
  let frame = 0;
  let last = 0;
  let elapsed = 0;
  function draw(time = 0) {
    frame = 0;
    const dt = Math.min((time - last) / 1000, .035); last = time;
    if (!paused && visible) elapsed += dt;
    for (const bean of beans) {
      const { mesh, anchor, rotation, phase, speed } = bean;
      const drift = elapsed * speed + phase;
      // Every movement stays close to its anchor; nothing falls or respawns.
      mesh.position.set(
        anchor.x + Math.sin(drift * .8) * .12,
        anchor.y + Math.sin(drift) * .17,
        anchor.z + Math.cos(drift * .65) * .08
      );
      mesh.rotation.set(
        rotation.x + Math.sin(drift * .6) * .09,
        rotation.y + Math.cos(drift * .7) * .14,
        rotation.z + Math.sin(drift * .5) * .07
      );
    }
    renderer.render(scene, camera);
    if (!paused && visible) frame = requestAnimationFrame(draw);
  }
  function resume() { if (!frame) { last = performance.now(); frame = requestAnimationFrame(draw); } }
  function resize() {
    camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    camera.updateMatrixWorld();
    const columns = camera.aspect < .75 ? 3 : 5;
    beans.forEach((bean, index) => {
      bean.mesh.visible = index < columns * 4;
      const col = index % columns;
      const row = Math.floor(index / columns);
      const x = -.84 + col * (1.68 / (columns - 1)) + bean.jitterX;
      const y = .80 - row * (1.6 / 3) + bean.jitterY;
      const ray = new THREE.Vector3(x, y, .5).unproject(camera).sub(camera.position).normalize();
      const distance = (bean.depth - camera.position.z) / ray.z;
      bean.anchor.copy(camera.position).addScaledVector(ray, distance);
    });
    if (paused) draw(performance.now());
  }
  window.addEventListener('resize', resize);
  window.addEventListener('coffee-motion', event => { paused = event.detail.paused; resume(); });
  document.addEventListener('visibilitychange', () => { visible = !document.hidden; if (visible) resume(); });
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault(); paused = true;
    document.querySelector('.scene').classList.add('scene-fallback');
  });
  resize(); resume();
} catch (error) {
  document.querySelector('.scene').classList.add('scene-fallback');
  document.querySelector('#motion-toggle').hidden = true;
  console.warn('3D unavailable; static coffee background enabled.', error.message);
}
