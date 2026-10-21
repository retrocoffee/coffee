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
      jitterX: (Math.random() - .5) * .10,
      jitterY: (Math.random() - .5) * .10
    });
  }
  function draw() {
    for (const { mesh, anchor } of beans) mesh.position.copy(anchor);
    renderer.render(scene, camera);
  }
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
    draw();
  }
  window.addEventListener('resize', resize);
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault();
    document.querySelector('.scene').classList.add('scene-fallback');
  });
  resize();
} catch (error) {
  document.querySelector('.scene').classList.add('scene-fallback');
  console.warn('3D unavailable; static coffee background enabled.', error.message);
}
