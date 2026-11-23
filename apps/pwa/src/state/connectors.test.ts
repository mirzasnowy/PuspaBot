import { describe, expect, it } from "vitest";
import { channelList, CONNECTORS } from "./connectors";

describe("connector logos (Nero, 24 Nov — real official logos)", () => {
  it("every connector carries its official logo path", () => {
    expect(CONNECTORS.map((c) => c.name)).toEqual(["gmail", "drive", "calendar"]);
    for (const connector of CONNECTORS) {
      expect(connector.logo).toMatch(/^\/static\/logos\/[a-z]+\.png$/);
    }
  });

  it("every channel carries its official logo path", () => {
    const channels = channelList();
    expect(channels.map((c) => c.name)).toEqual([
      "Gmail",
      "Drive",
      "Calendar",
      "Telegram",
      "WhatsApp",
    ]);
    for (const channel of channels) {
      expect(channel.logo).toMatch(/^\/static\/logos\/[a-z]+\.png$/);
    }
  });
});
