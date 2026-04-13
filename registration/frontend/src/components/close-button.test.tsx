import { render, screen } from "@solidjs/testing-library";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CloseButton } from "./close-button";

describe("CloseButton", () => {
  it("renders a close button with the expected accessible label", () => {
    render(() => <CloseButton close={() => {}} />);
    const btn = screen.getByRole("button", { name: "Close" });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveClass("btn-close");
  });

  it("invokes close() when clicked", async () => {
    const close = vi.fn();
    render(() => <CloseButton close={close} />);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(close).toHaveBeenCalledTimes(1);
  });
});
