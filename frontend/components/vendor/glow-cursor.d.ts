import type {CSSProperties, HTMLAttributes, ReactNode} from 'react';

/**
 * Types for the vendored React Bits GlowCursor (glow-cursor.jsx), matching
 * its documented props table. Without these, TypeScript infers every prop
 * that has no default -- children, style, blendMode -- as required.
 */
export type GlowCursorProps = {
  /** Light colour where freshly drawn. Default '#5f8bff'. */
  color?: string;
  /** Colour the light cools to. Defaults to a deeper shade of `color`. */
  secondaryColor?: string;
  intensity?: number;
  /** Trail thickness in px. */
  trailWidth?: number;
  /** Seconds before the light fades. */
  linger?: number;
  glow?: number;
  /** White-hot core near the cursor. */
  hotspot?: number;
  /** 0..1 -- how tightly the light follows the pointer. */
  followSpeed?: number;
  grain?: number;
  clickBurst?: boolean;
  quality?: 'low' | 'medium' | 'high';
  /** 'light' renders ink on light backgrounds. */
  theme?: 'dark' | 'light';
  blendMode?: CSSProperties['mixBlendMode'];
  maxDevicePixelRatio?: number;
  /** Stops drawing new light while keeping what is already there. */
  enabled?: boolean;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
} & Omit<HTMLAttributes<HTMLDivElement>, 'color' | 'children' | 'className' | 'style'>;

declare const GlowCursor: (props: GlowCursorProps) => JSX.Element;
export default GlowCursor;
