import * as THREE from './assets/three.module.js';
const canvas = document.querySelector('#coffee-scene');
try {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.35;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x28190f, .037);
  const camera = new THREE.PerspectiveCamera(43, 1, .1, 100);
  camera.position.set(0, 5.8, 20);
  camera.lookAt(0, 4.4, 0);
  scene.add(new THREE.AmbientLight(0xd5a779, 2.1));
  const key = new THREE.DirectionalLight(0xffd8a1, 5);
  key.position.set(-6, 10, 8); scene.add(key);
  const rim = new THREE.DirectionalLight(0xfbb86d, 3);
  rim.position.set(7, 4, -5); scene.add(rim);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(100, 80), new THREE.MeshBasicMaterial({ color: 0x28180e, transparent: true, opacity: .36, depthWrite: false }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -.02; scene.add(floor);
  // Sculpt an actual bean with an irregular crease on the front surface.
  const geometry = new THREE.SphereGeometry(1, 48, 40);
  const vertices = geometry.attributes.position;
  for (let i = 0; i < vertices.count; i++) {
    const x = vertices.getX(i), y = vertices.getY(i), z = vertices.getZ(i);
    const creaseX = .1 * Math.sin(y * 3.3);
    const groove = z > 0 ? .27 * Math.exp(-Math.pow((x - creaseX) / .095, 2)) * Math.pow(Math.max(0, 1 - y * y), .4) : 0;
    const irregularity = 1 + .018 * Math.sin(y * 19 + x * 13) * Math.cos(z * 17);
    vertices.setXYZ(i, x * .68 * irregularity, y * irregularity, z * .48 - groove);
  }
  geometry.computeVertexNormals();
  const textureCanvas = document.createElement('canvas'); textureCanvas.width = textureCanvas.height = 128;
  const ctx = textureCanvas.getContext('2d');
  const pixels = ctx.createImageData(128, 128);
  for (let i = 0; i < pixels.data.length; i += 4) {
    const shade = 105 + Math.random() * 90;
    pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = shade;
    pixels.data[i + 3] = 255;
  }
  ctx.putImageData(pixels, 0, 0);
  const texture = new THREE.CanvasTexture(textureCanvas);
  const material = new THREE.MeshStandardMaterial({ color: 0x754124, roughness: .48, metalness: .08, bumpMap: texture, bumpScale: .065 });
  const reflectionMaterial = material.clone(); reflectionMaterial.transparent = true; reflectionMaterial.opacity = .19; reflectionMaterial.depthWrite = false;
  const beans = [];
  let viewWidth = 20;
  function newX() { return (Math.random() < .5 ? -1 : 1) * (viewWidth * (.24 + Math.random() * .3)); }
  for (let i = 0; i < 24; i++) {
    const mesh = new THREE.Mesh(geometry, material);
    const scale = .28 + Math.random() * .57;
    mesh.scale.setScalar(scale);
    mesh.position.set(newX(), .8 + Math.random() * 13, -5 + Math.random() * 7);
    mesh.rotation.set(Math.random() * 2, Math.random() * 4, Math.random() * 6);
    scene.add(mesh);
    const mirror = new THREE.Mesh(geometry, reflectionMaterial); scene.add(mirror);
    beans.push({ mesh, mirror, scale, velocity: -.4 - Math.random(), spin: (Math.random() - .5) * 1.2, bounces: 0, wait: 0 });
  }
  let paused = document.querySelector('#motion-toggle').getAttribute('aria-pressed') === 'true';
  let visible = !document.hidden;
  let frame = 0;
  let last = 0;
  function draw(time = 0) {
    frame = 0;
    const dt = Math.min((time - last) / 1000, .035); last = time;
    for (const bean of beans) {
      const { mesh, mirror, scale } = bean;
      if (!paused && visible) {
        bean.velocity -= 2.6 * dt;
        mesh.position.y += bean.velocity * dt;
        mesh.rotation.x += bean.spin * dt;
        mesh.rotation.z += .34 * dt;
        if (mesh.position.y < scale * .65) {
          mesh.position.y = scale * .65;
          bean.velocity = Math.abs(bean.velocity) * .57;
          bean.bounces++;
          if (bean.bounces > 3) bean.wait += dt;
          if (bean.wait > 1.1) {
            mesh.position.set(newX(), 13 + Math.random() * 4, -5 + Math.random() * 7);
            bean.velocity = -.5; bean.bounces = 0; bean.wait = 0;
          }
        }
      }
      mirror.position.copy(mesh.position); mirror.position.y = -mesh.position.y;
      mirror.rotation.set(-mesh.rotation.x, mesh.rotation.y, -mesh.rotation.z);
      mirror.scale.set(scale, -scale, scale);
    }
    renderer.render(scene, camera);
    if (!paused && visible) frame = requestAnimationFrame(draw);
  }
  function resume() { if (!frame) { last = performance.now(); frame = requestAnimationFrame(draw); } }
  function resize() {
    camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    const nextWidth = 2 * Math.tan(THREE.MathUtils.degToRad(43 / 2)) * 20 * camera.aspect;
    beans.forEach(bean => { bean.mesh.position.x *= nextWidth / viewWidth; });
    viewWidth = nextWidth;
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
