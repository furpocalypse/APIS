import { QueryClientProvider } from "@tanstack/solid-query";
import { RouterProvider, createRouter } from "@tanstack/solid-router";
import { render } from "solid-js/web";
import "vite/modulepreload-polyfill";

// solid-devtools is a side-effecting import that installs a runtime
// inspector. Loading it dynamically inside an `import.meta.env.DEV` gate
// means Vite never resolves the module in a prod build — it stays out of
// the bundle entirely. OWASP A05 / Third-Party-JS Cheat Sheet.
if (import.meta.env.DEV) {
  // eslint-disable-next-line @typescript-eslint/no-floating-promises
  import("solid-devtools");
}

import "./index.scss";
import { queryClient } from "./queries";
import { routeTree } from "./routeTree.gen";
import { initSentry } from "./sentry";

const router = createRouter({
  routeTree,
  context: { queryClient },
  scrollRestoration: true,
  defaultPreload: "intent",
  defaultPreloadStaleTime: 0,
});

declare module "@tanstack/solid-router" {
  interface Register {
    router: typeof router;
  }
}

initSentry();

const rootElement = document.getElementById("root")!;
render(
  () => (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  ),
  rootElement,
);
