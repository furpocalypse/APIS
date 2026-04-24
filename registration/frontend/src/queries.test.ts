import { describe, expect, it } from "vitest";

import { api, queryClient } from "./queries";

describe("queries module", () => {
  it("exposes a QueryClient with retry=3 as the default", () => {
    const defaults = queryClient.getDefaultOptions();
    expect(defaults.queries?.retry).toBe(3);
  });

  it("exposes a ky instance that extends the base", () => {
    // ky.extend returns a function-like object with .extend, .get, .post, ...
    expect(typeof api).toBe("function");
    expect(typeof api.extend).toBe("function");
    expect(typeof api.get).toBe("function");
    expect(typeof api.post).toBe("function");
  });
});
