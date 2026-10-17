'use client';

import {useMemo, useRef} from 'react';
import {useFrame} from '@react-three/fiber';
import * as THREE from 'three';
import {getScroll} from '@/lib/scroll-store';

/**
 * Crystal shards orbiting the ruby N.
 *
 * Rendered as ONE InstancedMesh -- a single draw call however many shards --
 * and parented to the gem's group, so they travel the whole page with it,
 * shrink and dim with it, and need no choreography of their own.
 *
 * Their motion follows the same three-channel law as the gem:
 *   idle      each shard orbits, bobs and tumbles on its own clock. Never stops.
 *   velocity  fast scrolling flings the ring outward; it settles back as the
 *             shared velocity decays.
 *   scrub     inherited from the parent group (position, scale, opacity).
 *
 * Every per-shard parameter is seeded deterministically, so the constellation
 * is identical on every visit instead of reshuffling on each mount.
 */
type Seed = {
  radius: number;
  angle: number;
  speed: number;
  y: number;
  bob: number;
  phase: number;
  depth: number;
  size: number;
  spin: THREE.Vector3;
};

/** Small deterministic PRNG (mulberry32): same shards on every load. */
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export default function Shards({
  count,
  reduced,
  colors,
}: {
  count: number;
  reduced: boolean;
  /** [ruby, deep, chip] -- resolved brand tokens. */
  colors: [string, string, string];
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const spread = useRef(1);

  const seeds = useMemo<Seed[]>(() => {
    const r = rng(7);
    return Array.from({length: count}, () => ({
      // Outside the N's silhouette (half-height ~1.35) so no shard ever
      // sits in front of the letterform's face.
      radius: 1.6 + r() * 2.3,
      angle: r() * Math.PI * 2,
      speed: (0.05 + r() * 0.09) * (r() > 0.5 ? 1 : -1),
      y: (r() - 0.5) * 4,
      bob: 0.06 + r() * 0.14,
      phase: r() * Math.PI * 2,
      depth: 0.45 + r() * 0.5,
      // Mostly fine dust, a few larger crystals: the power curve keeps big
      // shards rare so the ring reads as glitter, not a hailstorm.
      size: 0.025 + Math.pow(r(), 2.6) * 0.15,
      spin: new THREE.Vector3(r() - 0.5, r() - 0.5, r() - 0.5).normalize().multiplyScalar(0.3 + r() * 0.9),
    }));
  }, [count]);

  // Scratch objects, allocated once: the render loop must not create garbage.
  const dummy = useMemo(() => new THREE.Object3D(), []);

  const geometry = useMemo(() => new THREE.OctahedronGeometry(1, 0), []);

  // Mostly ruby, some deep crimson, a few near-white chips for contrast.
  const instanceColors = useMemo(() => {
    const r = rng(11);
    const palette = colors.map((c) => new THREE.Color(c));
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const roll = r();
      const c = roll < 0.55 ? palette[0] : roll < 0.82 ? palette[1] : palette[2];
      c.toArray(arr, i * 3);
    }
    return new THREE.InstancedBufferAttribute(arr, 3);
  }, [colors, count]);

  useFrame((state, delta) => {
    const m = mesh.current;
    if (!m) return;
    const t = reduced ? 0 : state.clock.elapsedTime;
    const dt = Math.min(delta, 1 / 30);

    // Fling outward with scroll speed, then settle as velocity decays.
    const v = Math.min(1, Math.abs(getScroll().velocity) / 40);
    spread.current = THREE.MathUtils.damp(spread.current, 1 + v * 0.45, 2.4, dt);

    for (let i = 0; i < seeds.length; i++) {
      const s = seeds[i];
      const a = s.angle + t * s.speed;
      const r = s.radius * spread.current;
      dummy.position.set(Math.cos(a) * r, s.y + Math.sin(t * 0.6 + s.phase) * s.bob, Math.sin(a) * r * s.depth);
      dummy.rotation.set(t * s.spin.x + s.phase, t * s.spin.y, t * s.spin.z);
      dummy.scale.setScalar(s.size);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[geometry, undefined, count]} frustumCulled={false}>
      <instancedBufferAttribute attach="instanceColor" args={[instanceColors.array, 3]} />
      {/* No transmission pass: at this size it is invisible and doubles the
          cost. Mirror-hard reflections of the same strip-light studio make
          each shard flash as it tumbles past a light. */}
      <meshPhysicalMaterial
        roughness={0}
        metalness={0.15}
        clearcoat={1}
        clearcoatRoughness={0}
        ior={2.42}
        envMapIntensity={2.6}
        flatShading
      />
    </instancedMesh>
  );
}
