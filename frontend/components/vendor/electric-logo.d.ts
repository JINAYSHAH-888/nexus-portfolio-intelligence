import type {CSSProperties} from 'react';

/** Types for the vendored React Bits ElectricLogo, matching its props table. */
export type ElectricLogoProps = {
  /** Image to trace. SVG / transparent PNG by alpha; opaque by keying the backdrop. */
  src?: string;
  /** Hot core of each strand. HEX only -- the component parses it with hexToRgb. */
  color?: string;
  /** Glow, bloom and fill colour. HEX only. */
  glowColor?: string;
  /** Logo size as a fraction of the container; glow and arcs extend beyond. */
  scale?: number;
  intensity?: number;
  glow?: number;
  /** Main strand width in px. */
  thickness?: number;
  /** 1..6 filaments tracing the outline. */
  strands?: number;
  bend?: number;
  crackle?: number;
  /** How often arcs leap off the edge; 0 disables. */
  arcs?: number;
  flicker?: number;
  fill?: number;
  /** 0 freezes the frame. */
  speed?: number;
  interactive?: boolean;
  cursorIntensity?: number;
  cursorRadius?: number;
  /** 'light' renders crisp electric ink for light backgrounds. */
  theme?: 'dark' | 'light';
  onRender?: (canvas: HTMLCanvasElement) => void;
  className?: string;
  style?: CSSProperties;
};

declare const ElectricLogo: (props: ElectricLogoProps) => JSX.Element;
export default ElectricLogo;
