import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts", "tests/**/*.test.ts"],
    testTimeout: 15000,
    alias: {
      "#client": path.resolve(__dirname, "src/client.ts"),
    },
  },
});
