/** Canonical Smara orb mark. Kept as SVG so it stays crisp in the app and in favicons. */
type Props = { size?: number; animate?: boolean };

export default function SmaraLogo({ size = 28, animate = false }: Props) {
  const id = `smara-orb-${size}`;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"
      className={animate ? "smara-logo smara-logo-animated" : "smara-logo"} aria-label="Smara">
      <defs>
        <radialGradient id={`${id}-light`} cx="30%" cy="24%" r="78%">
          <stop offset="0" stopColor="#efffb0" />
          <stop offset="0.5" stopColor="#b7ed2a" />
          <stop offset="0.82" stopColor="#72bf18" />
          <stop offset="1" stopColor="#0e5a35" />
        </radialGradient>
        <linearGradient id={`${id}-top`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#073d2b" stopOpacity="0.95" />
          <stop offset="0.72" stopColor="#176c37" stopOpacity="0.3" />
          <stop offset="1" stopColor="#176c37" stopOpacity="0" />
        </linearGradient>
        <filter id={`${id}-shadow`} x="-30%" y="-30%" width="160%" height="170%">
          <feDropShadow dx="0" dy="2" stdDeviation="2" floodColor="#062d20" floodOpacity="0.28" />
        </filter>
      </defs>
      <circle cx="32" cy="32" r="27" fill={`url(#${id}-light)`} filter={`url(#${id}-shadow)`} />
      <ellipse cx="32" cy="17" rx="25" ry="13" fill={`url(#${id}-top)`} opacity="0.82" />
      <ellipse cx="24" cy="20" rx="8" ry="4" fill="#f7ffd1" opacity="0.2" />
    </svg>
  );
}
