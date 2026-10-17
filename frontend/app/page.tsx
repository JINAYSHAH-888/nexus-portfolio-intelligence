import Curtain from '@/components/landing/curtain';
import LandingNav from '@/components/landing/landing-nav';
import GridField from '@/components/landing/grid-field';
import Opening from '@/components/landing/opening';
import Evidence from '@/components/landing/evidence';
import Engines from '@/components/landing/engines';
import Ledger from '@/components/landing/ledger';
import Close from '@/components/landing/close';
import GemLayer from '@/components/landing/gem-layer';
import RedGlow from '@/components/landing/red-glow';
import GemBloom from '@/components/landing/gem-bloom';
import GemReticle from '@/components/landing/gem-reticle';
import DataTicker, {type TickerItem} from '@/components/landing/data-ticker';
import {facts, formatAsOf, shortStock} from '@/lib/landing-series';

/* Words only, so the page does not fill up with figures. The first
 * band is the four bank names -- the headline, as texture -- the second is
 * what the product promises. */
const BANKS_BAND: TickerItem[] = facts.mostHeld.map((b, i) =>
  i % 2 ? {value: shortStock(b.stock)} : {label: shortStock(b.stock)},
);
const PROMISE_BAND: TickerItem[] = [
  {label: 'No black boxes'},
  {value: 'Every layer shown'},
  {label: 'From the factsheets'},
  {value: 'Pass or fail, published'},
];

/**
 * The landing page: one continuous scroll in five movements, with the ruby N
 * as its spine. Copy is kept to a line or two per movement, and the only
 * figures on the page are the three in the evidence beat.
 *
 * Layer order, back to front:
 *   body background
 *   grid field   (fixed)   -- the 12-column hairlines and their red shades
 *   red glow     (fixed)   -- ink trail behind the pointer (React Bits)
 *   ruby N       (fixed)   -- ONE canvas for the whole page, with its shards
 *   reticle      (fixed)   -- crosshair brackets tracking the gem
 *   content      (z-10)    -- the six movements and two data ticker bands
 *   nav          (fixed)
 *   curtain      (fixed)   -- entrance, CSS-only, gone after 1.6s
 *
 * This is a server component: every movement's text is in the initial HTML.
 * Only the parts that genuinely need the client (the gem, the pinned beats,
 * the converging headings) hydrate.
 */
export default function LandingPage() {
  const asOf = formatAsOf(facts.asOf);
  return (
    // landing-bleed: full-width grid (see globals.css). The app screens keep
    // the 1400px reading width; the landing page is a canvas, not a column.
    <div className="landing-bleed relative min-h-dvh bg-bg text-text">
      <Curtain asOf={asOf} />
      <GridField />
      <RedGlow />
      <GemBloom />
      <GemLayer />
      <GemReticle />
      <LandingNav />
      <main id="main" className="relative z-10 overflow-x-clip">
        <Opening />
        <DataTicker items={BANKS_BAND} />
        <Evidence />
        <Engines />
        <DataTicker items={PROMISE_BAND} direction={-1} />
        <Ledger />
        <Close />
      </main>
    </div>
  );
}
