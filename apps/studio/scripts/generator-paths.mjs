export function toPortableFixtureKey(relativePath, nativeSeparator) {
  if (
    typeof relativePath !== "string" ||
    relativePath.length === 0 ||
    (nativeSeparator !== "/" && nativeSeparator !== "\\")
  ) {
    throw new Error("Fixture-map keys require a non-empty host-relative path");
  }
  const portable = relativePath.split(nativeSeparator).join("/");
  const components = portable.split("/");
  if (
    portable.startsWith("/") ||
    portable.includes("\\") ||
    components.some((component) => component === "" || component === "." || component === "..")
  ) {
    throw new Error(`Fixture-map key is not repository-portable: ${relativePath}`);
  }
  return portable;
}
