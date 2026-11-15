import { describe, expect, it } from "vitest";
import { textFileProblem } from "../scripts/check-deploy";

const UTF8 = "text/markdown; charset=utf-8";
const hebrew = new TextEncoder().encode("# ויקי\n");

describe("after-deploy check of the site's text files", () => {
  it("accepts a file served as UTF-8 with Hebrew in it", () => {
    expect(textFileProblem(200, UTF8, hebrew)).toBeNull();
    expect(textFileProblem(200, "text/plain; Charset=UTF-8", hebrew)).toBeNull();
  });

  it("refuses a content type without a character set: the browser guesses and Hebrew shows as garbage", () => {
    expect(textFileProblem(200, "text/markdown", hebrew)).toContain("charset=utf-8");
    expect(textFileProblem(200, null, hebrew)).toContain("charset=utf-8");
  });

  it("refuses a missing file, bytes that are not UTF-8 and a body without Hebrew", () => {
    expect(textFileProblem(404, UTF8, hebrew)).toBe("status 404");
    expect(textFileProblem(200, UTF8, new Uint8Array([0xe5, 0xe9, 0xf7]))).toContain("not valid UTF-8");
    expect(textFileProblem(200, UTF8, new TextEncoder().encode("wiki"))).toContain("no Hebrew");
  });
});
