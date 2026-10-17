'use client';

import dynamic from 'next/dynamic';

// three.js, R3F and drei stay out of the first-load bundle entirely: the page
// is readable and complete before a single byte of 3D arrives.
const RubyNScene = dynamic(() => import('@/components/scene/ruby-n'), {
  ssr: false,
  loading: () => null,
});

/** The fixed, full-viewport layer the gem lives in for the whole page. */
export default function GemLayer() {
  return <RubyNScene className="pointer-events-none fixed inset-0 z-0" />;
}
