import type { VaultFile } from "../types";

/** Folder tree derived from flat file paths (the API sends no directory entries). */

export interface TreeDir {
  kind: "dir";
  name: string;
  path: string;
  children: TreeNode[];
}

export interface TreeLeaf {
  kind: "file";
  name: string;
  path: string;
  file: VaultFile;
}

export type TreeNode = TreeDir | TreeLeaf;

export function buildTree(files: VaultFile[]): TreeNode[] {
  const root: TreeDir = { kind: "dir", name: "", path: "", children: [] };
  const dirs = new Map<string, TreeDir>([["", root]]);

  const dirFor = (path: string): TreeDir => {
    const hit = dirs.get(path);
    if (hit) return hit;
    const slash = path.lastIndexOf("/");
    const parent = dirFor(slash < 0 ? "" : path.slice(0, slash));
    const dir: TreeDir = {
      kind: "dir",
      name: slash < 0 ? path : path.slice(slash + 1),
      path,
      children: [],
    };
    parent.children.push(dir);
    dirs.set(path, dir);
    return dir;
  };

  for (const file of files) {
    const slash = file.path.lastIndexOf("/");
    const parent = dirFor(slash < 0 ? "" : file.path.slice(0, slash));
    parent.children.push({
      kind: "file",
      name: slash < 0 ? file.path : file.path.slice(slash + 1),
      path: file.path,
      file,
    });
  }

  const sortRec = (nodes: TreeNode[]): void => {
    nodes.sort((a, b) =>
      a.kind !== b.kind ? (a.kind === "dir" ? -1 : 1) : a.name.localeCompare(b.name),
    );
    for (const n of nodes) if (n.kind === "dir") sortRec(n.children);
  };
  sortRec(root.children);
  return root.children;
}
