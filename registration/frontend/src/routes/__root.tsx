import { TanStackDevtools } from "@tanstack/solid-devtools";
import { hotkeysDevtoolsPlugin } from "@tanstack/solid-hotkeys-devtools";
import type { QueryClient } from "@tanstack/solid-query";
import { SolidQueryDevtoolsPanel } from "@tanstack/solid-query-devtools";
import {
  HeadContent,
  Outlet,
  createRootRouteWithContext,
} from "@tanstack/solid-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/solid-router-devtools";
import { type Component } from "solid-js";

const RootLayout: Component = () => {
  return (
    <>
      <HeadContent />
      <Outlet />
      {/*
        Devtools are dev-only. The render is gated on import.meta.env.DEV
        so Vite replaces it with `false &&` in production builds, which
        esbuild dead-code-eliminates. Combined with the package.json move
        of these modules to devDependencies, this keeps internal state
        and stack-trace introspection out of the prod bundle.
        OWASP A05 / Third-Party-JS Management Cheat Sheet.
      */}
      {import.meta.env.DEV && (
        <TanStackDevtools
          plugins={[
            {
              name: "TanStack Query",
              render: <SolidQueryDevtoolsPanel />,
            },
            {
              name: "TanStack Router",
              render: <TanStackRouterDevtoolsPanel />,
            },
            hotkeysDevtoolsPlugin(),
          ]}
          config={{ position: "bottom-left" }}
        />
      )}
    </>
  );
};

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()(
  {
    component: RootLayout,
  },
);
