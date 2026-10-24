import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { GlobeBackdrop, GLOBE_STILL, GLOBE_VIDEO } from "../features/landing/GlobeBackdrop";
import { ApiError, api } from "../services/api";
import { describeAction } from "../features/incidents/shared";

function mockReducedMotion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("reduce") ? reduced : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GlobeBackdrop", () => {
  it("shows only the still image when the user prefers reduced motion", () => {
    mockReducedMotion(true);
    const { container } = render(<GlobeBackdrop />);
    expect(container.querySelector("video")).toBeNull();
    expect(container.querySelector("img")?.getAttribute("src")).toBe(GLOBE_STILL);
  });

  it("layers a muted, looping video over the still and keeps it hidden until it plays", () => {
    mockReducedMotion(false);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const { container } = render(<GlobeBackdrop />);
    const video = container.querySelector("video")!;
    const img = container.querySelector("img")!;
    expect(video.getAttribute("src")).toBe(GLOBE_VIDEO);
    expect(video.muted).toBe(true);
    expect(video.loop).toBe(true);
    expect(video.className).toContain("opacity-0");
    // Identical geometry for both layers so the hand-off cannot jump.
    const geometry = (el: Element) => el.className.split(" ").filter((c) => c.startsWith("absolute") || c.startsWith("object-") || c.startsWith("inset") || c.startsWith("h-") || c.startsWith("w-")).sort().join(" ");
    expect(geometry(video)).toBe(geometry(img));
  });

  it("falls back to a CSS glow when the still fails and video is unavailable", () => {
    mockReducedMotion(true);
    const { container } = render(<GlobeBackdrop />);
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[class*='radial-gradient']")).not.toBeNull();
  });
});

describe("api client", () => {
  it("surfaces structured backend errors without inventing success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: "stale_approval", message: "City state changed after approval." } }),
      { status: 409 },
    )));
    await expect(api.execute("p1", "Ops")).rejects.toMatchObject({ status: 409, code: "stale_approval" });
  });

  it("reports an unreachable backend as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await api.state().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("network_error");
  });
});

describe("plan presentation", () => {
  it("describes route actions from their computed parameters", () => {
    const text = describeAction({
      action_type: "designate_evacuation_route",
      target_id: "I1",
      params: { node_path: ["I1", "I2", "I6"], length_m: 1700 },
    });
    expect(text).toBe("Evacuate I1 → I2 → I6 (1700 m)");
  });
});

describe("smoke", () => {
  it("renders text via testing-library", () => {
    render(<p>GridPulse</p>);
    expect(screen.getByText("GridPulse")).toBeInTheDocument();
  });
});
