/** Trever mark: white T and signal dot on navy. Same drawing as app/icon.svg (the favicon). */
export function LogoMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 2000 2000" width={size} height={size} className={className} aria-hidden focusable="false">
      <rect width="2000" height="2000" rx="470" fill="#0b1320" />
      <rect x="407" y="483" width="1117" height="271" rx="58" fill="#ffffff" />
      <rect x="830" y="700" width="271" height="341" rx="58" fill="#ffffff" />
      <circle cx="965" cy="1355" r="158" fill="#ff5230" />
    </svg>
  );
}
