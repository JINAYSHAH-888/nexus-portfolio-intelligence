'use client';

import {Component, useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {Canvas, useFrame} from '@react-three/fiber';
import {Environment, Lightformer, MeshTransmissionMaterial, useGLTF} from '@react-three/drei';
import * as THREE from 'three';
import {getScroll, getSpans, onSpans} from '@/lib/scroll-store';
import {buildKeys, emptyPose, samplePose} from '@/lib/scene-stations';
import {tokenColor} from '@/lib/theme-color';
import Shards from './shards';
import {publishGem} from '@/lib/gem-store';

/* ══════════════════════════════════════════════════════════════════════════
 * THE RUBY N — one persistent object for the whole page.
 *
 * Mounted ONCE in a fixed full-viewport layer, so it lives from the first
 * pixel of the page to the last and travels through every movement. Its
 * motion is three independent channels, ADDED together and never blended:
 *
 *   scrub    progress -> station track (lib/scene-stations.ts). Where the gem
 *            is. Freezes when scrolling stops -- correctly, it IS the page
 *            position.
 *   velocity how FAST you are scrolling. The gem leans into the scroll and its
 *            dispersion flares, then settles as velocity decays. This is what
 *            gives the motion weight instead of feeling like a scrubbed video.
 *   idle     time. A sway, a float, a wobble. Never reads scroll, never stops.
 *            This is the channel that keeps the gem alive at rest.
 *
 * Idle yaw is a SWAY, not a spin. A continuous spin would accumulate without
 * bound, so the gem would end the page facing an arbitrary direction and spend
 * time edge-on, where an N reads as a sliver. The sway keeps the letterform
 * legible and lets the closing station land exactly two turns after the
 * opening one.
 * ════════════════════════════════════════════════════════════════════════ */

const MODEL_URL = '/models/ruby-diamond-n.glb';
const CAMERA_Z = 8.4;
const FOV = 34;
const HALF_FOV_TAN = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
/** A jeweller's light box: nine vertical strips in a ring around the stone,
 *  unevenly spaced so the flashes do not pulse in a regular rhythm. */
const STRIPS = [0, 0.62, 1.31, 1.9, 2.55, 3.3, 4.02, 4.71, 5.5];

type TierName = 'high' | 'low';

/* Gem colours are resolved from the live brand tokens (three.js materials
 * cannot read CSS custom properties directly), so the stone follows the
 * palette instead of carrying a second copy of it. Mapped the way a real ruby
 * works: step 9 tints the light passing THROUGH (the bright facet edges), and
 * step 12 is the attenuation colour -- the deep core light loses itself in. */
function useGemPalette() {
  // --gem-* tokens are deliberately NOT themed (see globals.css): the stone
  // must look the same on the black landing and the off-white app.
  return useMemo(
    () => ({
      ruby: tokenColor('--gem-tint'),
      solid: tokenColor('--gem-solid'),
      core: tokenColor('--gem-core'),
      page: tokenColor('--bg'),
      studio: tokenColor('--gem-studio'),
      glintWarm: tokenColor('--gem-glint'),
      glintRed: tokenColor('--gem-tint'),
      chip: tokenColor('--gem-chip'),
    }),
    [],
  );
}

/** The studio environment once drei has rendered it, so refraction can
 *  sample it. Polled from the frame loop because scene.environment is a
 *  mutation, not React state -- the setter fires exactly once. */
function useSceneEnvironment() {
  const [env, setEnv] = useState<THREE.Texture | null>(null);
  useFrame((state) => {
    if (!env && state.scene.environment) setEnv(state.scene.environment);
  });
  return env;
}

/** Bake the GLB into one centred geometry with flat facet normals. */
function useGemGeometry() {
  const {scene} = useGLTF(MODEL_URL);
  return useMemo(() => {
    let source: THREE.BufferGeometry | null = null;
    scene.updateMatrixWorld(true);
    scene.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!source && mesh.isMesh) {
        source = mesh.geometry.clone().applyMatrix4(mesh.matrixWorld);
      }
    });
    if (!source) return null;
    const src = source as THREE.BufferGeometry;

    // The GLB ships POSITION and COLOR_0 but NO normals. A cut stone wants
    // FLAT facet normals -- one per triangle, not smoothed across edges --
    // which is what makes each facet catch light independently. De-indexing
    // first means computeVertexNormals produces exactly that.
    const geo = src.index ? src.toNonIndexed() : src;
    geo.deleteAttribute('color');
    geo.computeVertexNormals();
    geo.computeBoundingBox();
    const c = new THREE.Vector3();
    geo.boundingBox!.getCenter(c);
    geo.translate(-c.x, -c.y, -c.z);
    geo.computeBoundingSphere();
    return geo;
  }, [scene]);
}

function Gem({tier, reduced, mobile}: {tier: TierName; reduced: boolean; mobile: boolean}) {
  const group = useRef<THREE.Group>(null);
  const material = useRef<THREE.MeshPhysicalMaterial & {chromaticAberration?: number}>(null);
  const geometry = useGemGeometry();
  const palette = useGemPalette();
  const env = useSceneEnvironment();
  // Until the studio exists, refract the page colour: never a transparent
  // buffer, which would make the stone itself invisible (see below).
  const page = useMemo(() => new THREE.Color(palette.page), [palette.page]);

  // Station keyframes, rebuilt whenever the movements are re-measured.
  const keys = useRef(buildKeys(getSpans(), mobile));
  useEffect(() => onSpans((s) => (keys.current = buildKeys(s, mobile))), [mobile]);

  const pose = useRef(emptyPose());
  const dim = useRef(0);
  const born = useRef<number | null>(null);
  const screen = useRef(new THREE.Vector3());

  useFrame((state, delta) => {
    const g = group.current;
    if (!g) return;
    // A backgrounded tab resumes with a huge delta; clamp it or the gem
    // snaps a quarter of the page in one frame.
    const dt = Math.min(delta, 1 / 30);
    // Renderer, camera and size come from the frame's own state rather than a
    // captured useThree() value: the frame owns them, and mutating a hook's
    // return value is exactly what the React Compiler forbids.
    const {gl, camera, size} = state;
    const t = state.clock.elapsedTime;
    if (born.current === null) born.current = t;

    // ── scrub ────────────────────────────────────────────────────────────
    const s = getScroll();
    const p = samplePose(keys.current, s.progress, pose.current);

    // ── velocity ─────────────────────────────────────────────────────────
    const v = THREE.MathUtils.clamp(s.velocity / 45, -1, 1);

    // ── idle ─────────────────────────────────────────────────────────────
    const sway = reduced ? 0 : Math.sin(t * 0.31) * 0.42 + Math.sin(t * 0.13 + 1.2) * 0.22;
    const float = reduced ? 0 : Math.sin(t * 0.7) * 0.06;
    const wobble = reduced ? 0 : Math.sin(t * 0.47) * 0.05;

    // ── intro: the gem materialises rather than popping in ──────────────
    const introT = reduced ? 1 : THREE.MathUtils.clamp((t - born.current) / 1.6, 0, 1);
    const intro = 1 - Math.pow(1 - introT, 3);

    // Pose x/y are fractions of the half-viewport AT THE GEM'S DEPTH, so the
    // composition holds on any aspect ratio.
    const depth = (camera as THREE.PerspectiveCamera).position.z - p.z;
    const halfH = depth * HALF_FOV_TAN;
    const halfW = halfH * (size.width / Math.max(1, size.height));

    g.position.x = THREE.MathUtils.damp(g.position.x, p.x * halfW, 3.2, dt);
    g.position.y = THREE.MathUtils.damp(g.position.y, p.y * halfH + float, 3.2, dt);
    g.position.z = THREE.MathUtils.damp(g.position.z, p.z, 3.2, dt);

    g.rotation.y = THREE.MathUtils.damp(g.rotation.y, p.yaw + sway, 3.6, dt);
    // Lean INTO the scroll: pitch with direction, roll against it.
    g.rotation.x = THREE.MathUtils.damp(g.rotation.x, p.pitch + wobble + v * 0.24, 3.4, dt);
    g.rotation.z = THREE.MathUtils.damp(g.rotation.z, wobble * 0.6 - v * 0.12, 3.4, dt);

    const sc = p.scale * (0.86 + 0.14 * intro);
    g.scale.setScalar(THREE.MathUtils.damp(g.scale.x, sc, 3.2, dt));

    // One style write per frame, no React render: the gem recedes where the
    // page is dense and returns for the open moments.
    dim.current = THREE.MathUtils.damp(dim.current, p.dim * intro, 3, dt);
    gl.domElement.style.opacity = dim.current.toFixed(3);

    // Publish where the gem is on screen, for the DOM reticle that tracks it.
    // Projected from the group's world position: one Vector3, reused.
    screen.current.copy(g.position).project(camera);
    publishGem(
      (screen.current.x * 0.5 + 0.5) * size.width,
      (-screen.current.y * 0.5 + 0.5) * size.height,
      // Projected half-height of the N (1.35 world units at scale 1).
      ((1.35 * g.scale.x) / ((camera as THREE.PerspectiveCamera).position.z - g.position.z) / HALF_FOV_TAN) *
        (size.height / 2),
      g.rotation.y,
      dim.current,
    );

    // Dispersion flares with scroll speed and settles as velocity decays.
    const m = material.current;
    if (m && 'chromaticAberration' in m) {
      m.chromaticAberration = 0.09 + Math.abs(v) * 0.3;
    }
  });

  const shardColors = useMemo(
    () => [palette.ruby, palette.solid, palette.chip] as [string, string, string],
    [palette],
  );

  if (!geometry) return null;

  return (
    <group ref={group} dispose={null}>
      <Shards count={mobile ? 24 : 60} reduced={reduced} colors={shardColors} />
      <mesh geometry={geometry}>
        {tier === 'high' ? (
          <MeshTransmissionMaterial
            ref={material as never}
            // Refract the STUDIO, not the page. A plain white page transmits
            // the same colour through every facet and the stone reads as red
            // candy. A ring of bright strips against shadow means each facet
            // refracts something different -- that variation is the sparkle.
            // Always opaque: three.js multiplies a transmissive surface's
            // alpha by what it samples, so a transparent buffer would make
            // the stone itself invisible.
            background={env ?? page}
            backside
            backsideThickness={0.5}
            samples={6}
            resolution={512}
            transmission={1}
            thickness={0.85}
            ior={2.42}
            chromaticAberration={0.09}
            anisotropicBlur={0.02}
            distortion={0}
            temporalDistortion={0}
            roughness={0}
            clearcoat={1}
            clearcoatRoughness={0}
            color={palette.ruby}
            attenuationColor={palette.core}
            attenuationDistance={0.9}
            envMapIntensity={2.2}
          />
        ) : (
          // Low tier (phones, reduced motion, slow GPUs): no transmission
          // pass at all. Reflection + clearcoat still read as a polished
          // red stone, at a fraction of the fill cost.
          <meshPhysicalMaterial
            color={palette.solid}
            metalness={0.08}
            roughness={0.05}
            ior={2.42}
            clearcoat={1}
            clearcoatRoughness={0.03}
            specularIntensity={1}
            envMapIntensity={2.4}
            flatShading
          />
        )}
      </mesh>
    </group>
  );
}

/**
 * Adaptive quality. Samples frame time over 60-frame windows -- never single
 * frames -- and steps down: pixel ratio first (cheapest large win, least
 * visible loss on one object), then the transmission pass itself.
 */
function QualityGovernor({onDemote}: {onDemote: () => void}) {
  const samples = useRef<number[]>([]);
  const stage = useRef(0);

  useFrame((state, delta) => {
    const s = samples.current;
    s.push(delta * 1000);
    if (s.length < 60) return;
    const avg = s.reduce((a, b) => a + b, 0) / s.length;
    s.length = 0;
    if (avg <= 22) return;
    if (stage.current === 0) {
      stage.current = 1;
      state.gl.setPixelRatio(1);
    } else if (stage.current === 1) {
      stage.current = 2;
      onDemote();
    }
  });
  return null;
}

/** A WebGL failure must never break the page: it is complete without the gem. */
class SceneBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() {
    return {failed: true};
  }
  // Degrade silently for the VISITOR, never for the developer: a swallowed
  // WebGL error is otherwise indistinguishable from "the gem is not there".
  componentDidCatch(error: unknown) {
    console.error('[ruby-n] scene failed, page continues without it:', error);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function useMedia(query: string) {
  const [match, setMatch] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);
  return match;
}

export default function RubyNScene({className = ''}: {className?: string}) {
  const reduced = useMedia('(prefers-reduced-motion: reduce)');
  const mobile = useMedia('(max-width: 767px)');
  const [demoted, setDemoted] = useState(false);
  const [lost, setLost] = useState(false);
  const [generation, setGeneration] = useState(0);

  /* Context loss is routine, not exceptional: GPU resets, a backgrounded
   * mobile tab, or Chrome evicting the oldest of its ~16 contexts. Remount on
   * a FRESH context rather than leaving the page without its gem for good.
   * Capped, so a GPU that genuinely cannot hold a context does not loop. */
  useEffect(() => {
    if (!lost || generation >= 3) return;
    const id = window.setTimeout(() => {
      setGeneration((g) => g + 1);
      setLost(false);
    }, 600);
    return () => window.clearTimeout(id);
  }, [lost, generation]);
  const tier: TierName = reduced || mobile || demoted ? 'low' : 'high';
  const palette = useGemPalette();

  if (lost) return null;

  return (
    <div className={className} aria-hidden="true">
      <SceneBoundary>
        <Canvas
          key={generation}
          // Never "demand": the idle channel animates continuously by design,
          // and demand mode is exactly what would bring back the freeze.
          frameloop="always"
          dpr={[1, mobile ? 1.25 : 1.75]}
          camera={{position: [0, 0, CAMERA_Z], fov: FOV}}
          gl={{
            antialias: true,
            alpha: true,
            powerPreference: 'high-performance',
            toneMapping: THREE.ACESFilmicToneMapping,
            toneMappingExposure: 1.25,
          }}
          onCreated={({gl}) => {
            gl.outputColorSpace = THREE.SRGBColorSpace;
            // Start the CANVAS at 0 and let the frame loop fade it in. This
            // must be on gl.domElement, not <Canvas style>: R3F applies
            // `style` to its wrapper div, which the frame loop never touches,
            // so an opacity there hides the gem permanently.
            gl.domElement.style.opacity = '0';
            gl.domElement.addEventListener('webglcontextlost', (e) => {
              e.preventDefault();
              console.warn('[ruby-n] WebGL context lost; remounting on a fresh one.');
              setLost(true);
            });
          }}
        >
          {/* A gem is almost entirely what it reflects, so this IS the
              lighting. Bundled Lightformers rather than an HDRI preset (which
              downloads from a third-party CDN at runtime). Rendered once
              (frames=1): the lights never move, the stone does. */}
          <Environment resolution={256} frames={1}>
            {/* A light studio base: reflections should read as the bright
                room a white page implies, with the Lightformers as glints. */}
            <color attach="background" args={[palette.studio]} />
            {/* Narrow, intense strips -- not soft panels. A facet either
                catches a strip (a hard white flash) or it does not (deep
                red), and that alternation IS the sparkle. */}
            {STRIPS.map((a, i) => (
              <Lightformer
                key={i}
                intensity={i % 2 ? 7 : 12}
                position={[Math.sin(a) * 6, 0, Math.cos(a) * 6]}
                target={[0, 0, 0]}
                scale={[0.55, 10, 1]}
                color={i % 3 === 2 ? palette.glintWarm : undefined}
              />
            ))}
            <Lightformer intensity={12} position={[0, 4, 2]} scale={[10, 0.6, 1]} />
            <Lightformer intensity={8} position={[-5, 1, 1]} rotation-y={Math.PI / 2} scale={[0.6, 8, 1]} />
            <Lightformer intensity={6} position={[5, -1, 1]} rotation-y={-Math.PI / 2} scale={[0.5, 6, 1]} color={palette.glintWarm} />
            <Lightformer intensity={4} position={[0, -4, -2]} scale={[10, 0.8, 1]} color={palette.glintRed} />
            <Lightformer intensity={5} position={[-2, 2, -4]} rotation-y={Math.PI} scale={[3, 0.4, 1]} />
            <Lightformer form="ring" intensity={16} position={[2.5, 2.5, 3]} scale={0.6} />
          </Environment>
          <directionalLight position={[3, 4, 2]} intensity={1.2} />
          <directionalLight position={[-4, -1, -2]} intensity={0.5} color={palette.glintRed} />
          <Gem tier={tier} reduced={reduced} mobile={mobile} />
          {tier === 'high' && <QualityGovernor onDemote={() => setDemoted(true)} />}
        </Canvas>
      </SceneBoundary>
    </div>
  );
}

useGLTF.preload(MODEL_URL);
