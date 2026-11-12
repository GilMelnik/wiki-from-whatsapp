import { CfWorkerJsonSchemaValidator } from "@modelcontextprotocol/server/validators/cf-worker";
import { describe, expect, it } from "vitest";
import { mcpSettings } from "../src/settings";
import schema from "../wiki_index.schema.json";
import { index } from "./helpers";

const validate = new CfWorkerJsonSchemaValidator({ shortcircuit: false }).getValidator(schema);

describe("wiki_index.schema.json", () => {
  it("accepts the fixture", () => {
    const result = validate(index);
    expect(result.errorMessage).toBeUndefined();
    expect(result.valid).toBe(true);
  });

  it("pins the same index_version as config.json", () => {
    expect(schema.properties.index_version.const).toBe(mcpSettings.index_version);
  });

  it("requires the disclaimer line", () => {
    const { disclaimer: _disclaimer, ...withoutDisclaimer } = index;
    expect(validate(withoutDisclaimer).valid).toBe(false);
    expect(validate({ ...index, disclaimer: "" }).valid).toBe(false);
  });

  it("rejects a page without text, an absolute url, an unknown key and a bad relation", () => {
    const page = index.pages[0];
    const broken = (patch: object) => ({ ...index, pages: [{ ...page, ...patch }] });
    const { text: _text, ...noText } = page ?? { text: "" };
    expect(validate({ ...index, pages: [noText] }).valid).toBe(false);
    expect(validate(broken({ url: "/colombia/" })).valid).toBe(false);
    expect(validate(broken({ url: "https://gaysurrogacy.co.il/colombia/" })).valid).toBe(false);
    expect(validate(broken({ score: 1 })).valid).toBe(false);
    expect(validate(broken({ related: [{ id: "mexico", relation: "See Also" }] })).valid).toBe(false);
    expect(validate(broken({ status: "stable" })).valid).toBe(false);
  });
});
