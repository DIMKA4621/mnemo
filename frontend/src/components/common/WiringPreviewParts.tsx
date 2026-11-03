"use client";

import { Alert, Flex, Tag } from "antd";

/** Pieces shared by every "what will be written into this project" preview (Admin MCP wiring, add-bank init). */

export function FileTags({ files }: { files: string[] }) {
  return (
    <Flex gap={4} wrap style={{ marginTop: 4 }}>
      {files.map((f) => (
        <Tag key={f}>{f}</Tag>
      ))}
    </Flex>
  );
}

/** The tracked files plus the one command that untracks them. */
export function UntrackHint({ files }: { files: string[] }) {
  return (
    <>
      <FileTags files={files} />
      <pre className="tok-code">{`git rm --cached ${files.join(" ")}`}</pre>
    </>
  );
}

/** The backend's own English notes, verbatim — they carry paths and rule lines worth seeing as-is. */
export function NoteAlerts({ notes }: { notes: string[] }) {
  return (
    <>
      {notes.map((note) => (
        <Alert key={note} type="info" title={<span style={{ whiteSpace: "pre-wrap" }}>{note}</span>} />
      ))}
    </>
  );
}
