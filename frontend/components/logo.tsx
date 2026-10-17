"use client";

import { tokenColor } from "@/lib/theme-color";
import React from "react";
import MetallicPaint from "./metallic-paint";

/**
 * The "N" mark, rendered through the MetallicPaint WebGL shader instead of a
 * flat bordered box. Sized small (nav-scale) on purpose: the shader's detail
 * reads clearly even at 32-40px because the source SVG is a single bold
 * geometric stroke, not fine linework that would turn to noise at this size.
 */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <div
      className="shrink-0 border border-crimson/40 bg-crimson/5 overflow-hidden"
      style={{ width: size, height: size }}
    >
      <MetallicPaint
        imageSrc="/n-logo-mark.svg"
        renderSize={96}
        seed={7}
        scale={3}
        liquid={0.55}
        speed={0.25}
        brightness={1.6}
        contrast={0.65}
        fresnel={1.2}
        lightColor={tokenColor("--n-12")}
        darkColor={tokenColor("--brand-11")}
        tintColor={tokenColor("--accent")}
        patternSharpness={1.2}
        waveAmplitude={0.8}
        noiseScale={0.6}
        chromaticSpread={1.4}
        distortion={0.6}
        contour={0.15}
        className="w-full h-full"
      />
    </div>
  );
}
