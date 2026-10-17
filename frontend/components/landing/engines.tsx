import {Gauge, LineChart, Network, ShieldAlert} from 'lucide-react';
import Movement from './movement';
import ScrollText from './scroll-text';
import GradientCard from './gradient-card';
import Sparkline from '@/components/motion/sparkline';
import {facts, overlapCurve, plosWeights, studentT, survival} from '@/lib/landing-series';

/**
 * Movement 03. One card per engine: a name, one line, and one SHAPE.
 *
 * No figures: what stays is each engine's real
 * curve, computed from its own formula (lib/landing-series.ts) -- a picture
 * of the model rather than a number about it.
 */
const ENGINES = [
  {
    icon: Network,
    name: 'Holding overlap',
    line: 'Shared names and shared money, side by side.',
    spark: overlapCurve,
    summary: `Weighted overlap for all ${facts.pairs} scheme pairs, highest to lowest.`,
  },
  {
    icon: Gauge,
    name: 'PLOS score',
    line: 'Ten layers of similarity. Every one shown.',
    spark: plosWeights,
    summary: 'PLOS layer weights, largest first.',
  },
  {
    icon: ShieldAlert,
    name: 'Risk',
    line: 'Fat-tailed shocks, backtested in the open.',
    spark: studentT,
    summary: 'The fat-tailed Student-t shock distribution used for returns.',
  },
  {
    icon: LineChart,
    name: 'ARISE retirement',
    line: 'Your odds, with lifespans drawn honestly.',
    spark: survival,
    summary: 'Gompertz survival curve from age 60.',
  },
];

export default function Engines() {
  return (
    <Movement id="engines" density="open">
      <div className="grid-page">
        <div className="col-span-12 lg:col-span-7">
          <p className="label">The engines</p>
          <ScrollText
            id="engines-title"
            text="Four engines. Every layer shown."
            className="mt-4 max-w-[16ch] font-display text-display-2 font-semibold text-text"
          />
        </div>

        <ul className="col-span-12 mt-14 grid gap-4 sm:grid-cols-2 lg:col-span-7">
          {ENGINES.map((e) => {
            const Icon = e.icon;
            return (
              <li key={e.name} className="glass">
                <GradientCard className="gcard--bare relative z-[2] flex h-full flex-col p-7">
                  <Icon aria-hidden="true" className="h-5 w-5 text-accent-text" strokeWidth={1.5} />
                  <h3 className="mt-8 font-display text-step-3 font-semibold text-text">{e.name}</h3>
                  <p className="mt-2 text-step-0 text-text-secondary">{e.line}</p>
                  <Sparkline data={e.spark} summary={e.summary} width={220} height={44} className="mt-8 w-full" />
                </GradientCard>
              </li>
            );
          })}
        </ul>
      </div>
    </Movement>
  );
}
