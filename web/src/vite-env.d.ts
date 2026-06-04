/// <reference types="vite/client" />

// The ADS icon sprite registers a `<svg-icon>` custom element (see main.tsx).
declare namespace JSX {
  interface IntrinsicElements {
    "svg-icon": {
      icon: string;
      class?: string;
      slot?: string;
    };
  }
}
