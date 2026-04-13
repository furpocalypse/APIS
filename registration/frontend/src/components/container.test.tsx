import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";

import { Container } from "./container";

describe("Container", () => {
  it("falls back to .container when no UserSettings provider is present", () => {
    const { container } = render(() => <Container>hello</Container>);
    const div = container.querySelector("div");
    expect(div).not.toBeNull();
    expect(div!.className).toBe("container");
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});
