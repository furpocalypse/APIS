import { render, screen } from "@solidjs/testing-library";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./button";

describe("Button", () => {
  it("renders its children as the visible label", () => {
    render(() => <Button>Submit</Button>);
    expect(screen.getByRole("button")).toHaveTextContent("Submit");
  });

  it("is disabled when the native disabled prop is set", () => {
    render(() => <Button disabled>Go</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("is disabled and marked aria-busy while loading", () => {
    render(() => <Button loading>Saving</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
  });

  it("fires onClick when activated", async () => {
    const onClick = vi.fn();
    render(() => <Button onClick={onClick}>Tap</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("preserves any caller-supplied class on top of btn-loader", () => {
    render(() => <Button class="btn-primary">Go</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("btn-loader");
    expect(btn.className).toContain("btn-primary");
  });
});
