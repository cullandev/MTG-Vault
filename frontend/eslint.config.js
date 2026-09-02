import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      // ADR-003: OpenCV objects leak WASM memory unless .delete()d. Allocation is
      // only allowed inside quadDetect.ts, where every mat is registered with a
      // matScope() that deletes it on exit -- including on exceptions.
      'no-restricted-syntax': [
        'error',
        {
          selector: "NewExpression[callee.object.name='cv']",
          message:
            'OpenCV allocation outside src/scan/quadDetect.ts leaks WASM memory. ' +
            'Do the work inside a matScope() in quadDetect.ts (ADR-003).',
        },
        {
          selector: "CallExpression[callee.object.name='cv'][callee.property.name=/^matFrom/]",
          message:
            'cv.matFrom*() allocates a Mat that must be tracked by a matScope(). ' +
            'Only src/scan/quadDetect.ts may call it (ADR-003).',
        },
      ],
    },
  },
  {
    files: ['src/scan/quadDetect.ts'],
    rules: {
      // The one file allowed to allocate; every allocation there is track()-wrapped.
      'no-restricted-syntax': 'off',
    },
  },
)
