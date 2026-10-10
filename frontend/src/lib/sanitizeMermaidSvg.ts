import DOMPurify from "dompurify";

const PURIFY_OPTIONS = {
  USE_PROFILES: { svg: true, svgFilters: true },
  FORBID_TAGS: [
    "script", "foreignObject", "iframe", "object", "embed",
    "animate", "animateMotion", "animateTransform", "set",
  ],
  ALLOW_DATA_ATTR: false,
  RETURN_TRUSTED_TYPE: false,
};

const EXTERNAL_CSS_URL = /url\(\s*(['"]?)(.*?)\1\s*\)/gi;

function hasUnsafeCss(value: string): boolean {
  if (/@import\b|expression\s*\(/i.test(value)) return true;
  for (const match of value.matchAll(EXTERNAL_CSS_URL)) {
    if (!match[2].trim().startsWith("#")) return true;
  }
  return false;
}

/** Sanitize Mermaid's generated SVG before both display and download. */
export function sanitizeMermaidSvg(dirtySvg: string): string {
  const purified = DOMPurify.sanitize(dirtySvg, PURIFY_OPTIONS);
  const parsed = new DOMParser().parseFromString(purified, "image/svg+xml");
  const root = parsed.documentElement;
  if (parsed.querySelector("parsererror") || root?.localName !== "svg") {
    throw new Error("Mermaid generated invalid SVG.");
  }

  for (const element of [root, ...Array.from(root.querySelectorAll("*"))]) {
    if (element.localName.toLowerCase() === "style" && hasUnsafeCss(element.textContent ?? "")) {
      element.remove();
      continue;
    }
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (name.startsWith("on")) {
        element.removeAttribute(attribute.name);
      } else if ((name === "href" || name === "xlink:href" || name === "src") && !value.startsWith("#")) {
        // Keep only in-document SVG references: no remote fetches, scripts, or navigation.
        element.removeAttribute(attribute.name);
      } else if (hasUnsafeCss(value)) {
        // SVG presentation attributes (fill, filter, clip-path, etc.) can also
        // carry url() references, so validate every attribute value.
        element.removeAttribute(attribute.name);
      }
    }
  }
  return new XMLSerializer().serializeToString(root);
}
