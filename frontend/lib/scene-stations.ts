/**
 * The gem's journey through the page.
 *
 * Each movement of the landing page has a STATION: a pose the ruby N takes
 * while that movement is on screen. The choreography is
 *
 *     arrive at span.start  ->  HOLD until span.end  ->  travel to the next
 *
 * so the gem is still (apart from its idle breathing) while you read, and it
 * moves in the gaps between ideas. The travel is what tells you that you have
 * moved on -- which is how the page signals a new movement without drawing a
 * single dividing line.
 *
 * Stations are keyed to MEASURED movement spans (lib/scroll-store.ts), never
 * hardcoded percentages. ScrollTrigger pins insert pin-spacer height, so a
 * station written as `at: 0.20` would silently drift off its content the
 * moment a pin is added above it.
 *
 * Coordinates: x and y are fractions of the HALF-viewport at the gem's depth
 * (x = 0.5 means halfway from centre to the right edge), so a pose is correct
 * on any aspect ratio. z is world units toward (+) or away from (-) camera.
 */
import type {MovementId, MovementSpan} from './scroll-store';

export type Pose = {
  x: number;
  y: number;
  z: number;
  scale: number;
  /** Radians. Monotonic down the page, so the gem always turns one way. */
  yaw: number;
  pitch: number;
  /** Canvas opacity. The gem recedes where the page is dense. */
  dim: number;
};

type Station = {movement: MovementId; pose: Pose; mobile?: Partial<Pose>};

const TURN = Math.PI * 2;

/* Desktop poses alternate sides so content always has a clear column, and z
 * goes negative mid-page so dense data reads OVER the gem instead of fighting
 * it.
 *
 * Yaw: every station faces FRONT (a whole number of turns, give or take a
 * three-quarter angle toward the content), and each journey between stations
 * is one full spin. Fractional turns would park the stone backwards at some
 * stations -- a mirrored "И" -- which is wrong for a brand mark. The close is
 * the opening plus exactly four turns -- one per journey -- so it comes home
 * facing the way it started. */
export const STATIONS: Station[] = [
  {
    movement: 'opening',
    pose: {x: 0.6, y: -0.02, z: 0, scale: 1.3, yaw: 0.35, pitch: 0, dim: 1},
    // Clear of the 64px nav on a phone: the N's top serif must not touch it.
    mobile: {x: 0, y: 0.4, scale: 0.62, dim: 0.95},
  },
  {
    movement: 'evidence',
    pose: {x: -0.62, y: 0.04, z: -1.4, scale: 0.92, yaw: 0.35 + TURN * 1 - 0.55, pitch: 0.1, dim: 0.42},
  },
  {
    movement: 'engines',
    pose: {x: 0.64, y: 0, z: -1.2, scale: 0.96, yaw: 0.35 + TURN * 2 + 0.3, pitch: -0.08, dim: 0.55},
  },
  {
    movement: 'ledger',
    pose: {x: -0.62, y: 0.02, z: -1.6, scale: 0.9, yaw: 0.35 + TURN * 3 - 0.55, pitch: 0.12, dim: 0.48},
  },
  {
    movement: 'close',
    pose: {x: 0.6, y: -0.02, z: 0, scale: 1.3, yaw: 0.35 + TURN * 4, pitch: 0, dim: 1},
    mobile: {x: 0, y: 0.4, scale: 0.62, dim: 0.95},
  },
];

/** Applied to every station on narrow portrait screens: centre the gem and
 *  push it behind the text, because there is no free column to put it in. */
const MOBILE_DEFAULT: Partial<Pose> = {x: 0, scale: 0.6, dim: 0.22};

type Key = {at: number; pose: Pose};

/** Build the keyframe list: two keys per station (arrive, leave) = a hold. */
export function buildKeys(spans: readonly MovementSpan[], mobile: boolean): Key[] {
  const keys: Key[] = [];
  for (const st of STATIONS) {
    const span = spans.find((s) => s.id === st.movement);
    if (!span) continue;
    const pose = mobile ? {...st.pose, ...MOBILE_DEFAULT, ...st.mobile} : st.pose;
    const start = span.start;
    const end = Math.max(start, span.end);
    keys.push({at: start, pose}, {at: end, pose});
  }
  keys.sort((a, b) => a.at - b.at);
  return keys;
}

/** Quintic smootherstep: zero velocity AND acceleration at both ends, so the
 *  gem eases out of a hold and into the next one without a visible kink. */
const smoother = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** Sample the track at progress p into `out`. Allocation-free: this runs
 *  every frame, and the render loop must not create garbage. */
export function samplePose(keys: Key[], p: number, out: Pose): Pose {
  if (!keys.length) return out;
  if (p <= keys[0].at) return Object.assign(out, keys[0].pose);
  const last = keys[keys.length - 1];
  if (p >= last.at) return Object.assign(out, last.pose);

  let i = 0;
  while (i < keys.length - 2 && keys[i + 1].at < p) i++;
  const a = keys[i];
  const b = keys[i + 1];
  const span = b.at - a.at;
  const t = span > 1e-6 ? smoother((p - a.at) / span) : 1;

  out.x = lerp(a.pose.x, b.pose.x, t);
  out.y = lerp(a.pose.y, b.pose.y, t);
  out.z = lerp(a.pose.z, b.pose.z, t);
  out.scale = lerp(a.pose.scale, b.pose.scale, t);
  out.yaw = lerp(a.pose.yaw, b.pose.yaw, t);
  out.pitch = lerp(a.pose.pitch, b.pose.pitch, t);
  out.dim = lerp(a.pose.dim, b.pose.dim, t);
  return out;
}

export const emptyPose = (): Pose => ({...STATIONS[0].pose});
