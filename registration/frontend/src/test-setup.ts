import "@testing-library/jest-dom/vitest";
import { cleanup } from "@solidjs/testing-library";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

// JSDOM doesn't implement matchMedia; Kobalte components query it during
// render. Provide a minimal stub.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// JSDOM doesn't implement ResizeObserver; Solid router / Kobalte rely on it.
if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
  // @ts-expect-error intentional polyfill
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// MQTT is a heavy client that opens a network socket on import in the real
// app; stub it at the module boundary so admin-route tests don't try to talk
// to a broker.
vi.mock("mqtt", () => ({
  default: {
    connect: () => ({
      on: () => {},
      publish: () => {},
      subscribe: () => {},
      end: () => {},
    }),
  },
  connect: () => ({
    on: () => {},
    publish: () => {},
    subscribe: () => {},
    end: () => {},
  }),
}));

// Sentry is initialized at module-load in src/sentry.ts; mock it so tests
// don't try to open a real transport.
vi.mock("@sentry/solid", () => ({
  init: () => {},
  captureException: () => {},
  captureMessage: () => {},
  ErrorBoundary: (props: { children: unknown }) => props.children,
  withSentryErrorBoundary: (component: unknown) => component,
}));
