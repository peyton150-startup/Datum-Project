import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// Flat config (ESLint 9). The `lint` script in package.json is unrunnable
// without this file, and a declared script that always fails is worse than no
// script: it implies the frontend is linted when it is not.
export default tseslint.config(
  { ignores: ["dist", "enums.ts", "src/enums.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Unused args prefixed with _ are intentional, matching the tsconfig's
      // noUnusedParameters behaviour so the two tools agree.
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
);
