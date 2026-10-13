/** Vite's `?raw` imports (a file's source as a string), used by tests that read a source file. */
declare module "*?raw" {
  const text: string;
  export default text;
}
