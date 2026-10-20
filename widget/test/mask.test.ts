import { describe, expect, it } from "vitest";
import { maskText, maskUrl } from "../src/mask";

// The same cases as heed's tests/test_redact.py: the two rule sets must agree.
describe("maskText", () => {
  it.each([
    ["mail me at ana.b+x@example.co.uk", "ana.b+x@example.co.uk"],
    ["Authorization: Bearer abcdef123456", "abcdef123456"],
    ['{"api_key": "k-1234"}', "k-1234"],
    ["password=hunter2&next=/", "hunter2"],
    ["jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_nature", "eyJhbGciOiJIUzI1NiJ9"],
    ["key sk_live_abcdefghij1234", "sk_live_abcdefghij1234"],
    ["token ghp_" + "a".repeat(36), "ghp_" + "a".repeat(36)],
    ["card 4111-1111-1111-1111 ok", "4111-1111-1111-1111"],
    ["client 192.168.1.20 failed", "192.168.1.20"],
    ["call +33 6 12 34 56 78", "6 12 34 56 78"],
    ["id 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d", "3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d"],
    ["client_secret=abc", "abc"],
    ["AWS_SECRET_ACCESS_KEY: xyz", "xyz"],
    ['password="two words"', "words"],
    ["Authorization: Bearer abc", "abc"],
    ["token abc", "abc"],
    ["key sk-proj-abcdefghijklmnop", "abcdefghijklmnop"],
    ["aws wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"],
    ["call 415-555-1234", "555-1234"],
    ["from 2001:db8::1", "2001:db8::1"],
    ["ssn 123-45-6789", "45-6789"],
    ["at C:\\Users\\ana\\app.js", "ana"],
    ["at /home/ana/app.js", "ana"],
  ])("masks %s", (text, leaked) => {
    expect(maskText(text)).not.toContain(leaked);
  });

  it.each(["TypeError: x is undefined", "GET /api/items -> 500", "took 1234 ms", "v1.2.3", "12:30:45", "std::vector", "/api/users/me", "2024-10-20"])(
    "keeps %s",
    (text) => expect(maskText(text)).toBe(text),
  );

  it("agrees with the server on exact output", () => {
    expect(maskText("login failed for ana@example.com with token=abc123")).toBe("login failed for [email] with token=[redacted]");
    expect(maskText("Authorization: Bearer abc.def.ghi")).toBe("Authorization: [redacted]");
    expect(maskText('{"password": "hunter2", "user": "ana"}')).toBe('{"password": "[redacted]", "user": "ana"}');
    expect(maskText("auth_token=abc xsecretx: 'q r' pin=1234 spinner=2")).toBe("auth_token=[redacted] xsecretx: '[redacted]' pin=[redacted] spinner=2");
  });

  it.each(["1-".repeat(1000), "a".repeat(2000), "a@".repeat(1000), "1:".repeat(1000), "secret".repeat(333), "secret".repeat(333) + "=x"])(
    "stays fast on hostile input",
    (hostile) => {
      const start = performance.now();
      for (let i = 0; i < 50; i++) maskText(hostile);
      expect(performance.now() - start).toBeLessThan(1000);
    },
  );
});

describe("maskUrl", () => {
  it("drops credentials, fragment, query values, ids and handles", () => {
    expect(maskUrl("https://bob:pw@api.example:8443/u/ana@example.com?q=x&page=2#t")).toBe("https://api.example:8443/u/[email]?q=[redacted]&page=[redacted]");
    expect(maskUrl("https://app.example/u/12345/@ana/abcdef0123456789;jsessionid=Z/x")).toBe("https://app.example/u/[id]/[user]/[id]/x");
  });

  it("resolves relative URLs and hides data: content", () => {
    expect(maskUrl("/a?b=1", "https://app.example/page")).toBe("https://app.example/a?b=[redacted]");
    expect(maskUrl("data:text/plain,secret")).toBe("data:[redacted]");
  });

  it("masks a relative URL given no base, keeping it relative (#69)", () => {
    expect(maskUrl("/api/users/12345?a=1")).toBe("/api/users/[id]?a=[redacted]");
    expect(maskUrl("users/ana@example.com/@ana?t=s")).toBe("users/[email]/[user]?t=[redacted]");
    expect(maskUrl("?token=abc")).toBe("?token=[redacted]");
    expect(maskUrl("//cdn.example/u/12345?k=v")).toBe("//cdn.example/u/[id]?k=[redacted]");
    expect(maskUrl("\t//cdn.example/12345")).toBe("//cdn.example/[id]");
    expect(maskUrl("/a/..//evil/12345")).toBe("/.//evil/[id]");
  });
});

describe("maskUrl: tokens the server catches (parity with redact.py)", () => {
  const B = "https://app.example";
  it.each([
    ["/s/AbCdEfGhIjKlMnOpQrSt", "/s/[id]"],
    ["/s/token%2EwithDots%2E9x8y7z", "/s/[id]"],
    ["/r/AbCdEfGhIjKlMn%2FOpQrSt", "/r/[id]"],
    ["/auth/eyJhbGci.eyJzdWIi.sigpart", "/auth/[id]"],
    ["/help/terms-and-conditions-page", "/help/terms-and-conditions-page"],
    ["/reset/abcdefghijklmnopqrstuvwx", "/reset/[id]"],
    ["/docs/Getting-Started-With-Kubernetes", "/docs/Getting-Started-With-Kubernetes"],
    ["/wiki/Pneumonoultramicroscopicsilicovolcanoconiosis", "/wiki/Pneumonoultramicroscopicsilicovolcanoconiosis"],
    ["/files/MyQuarterlyReportFinal.pdf", "/files/MyQuarterlyReportFinal.pdf"],
    ["/x/50%/AbCdEfGhIjKlMnOpQrSt", "/x/50%/[id]"],
    ["/x/\u0663\u0664\u0665\u0666\u0667", "/x/[id]"],
  ])("%s", (path, expected) => {
    expect(maskUrl(path, B)).toBe(B + expected);
  });

  it("masks the payload of opaque schemes", () => {
    expect(maskUrl("mailto:ana@example.com?subject=hi")).toBe("mailto:[redacted]");
    expect(maskUrl("data:text/plain;base64,QUJD")).toBe("data:[redacted]");
  });
});

