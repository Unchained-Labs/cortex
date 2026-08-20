import { useEffect, useRef } from "react";
import { EditorView, keymap } from "@codemirror/view";
import { EditorState, Compartment } from "@codemirror/state";
import { basicSetup } from "codemirror";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { syntaxHighlighting } from "@codemirror/language";
import { oneDarkHighlightStyle } from "@codemirror/theme-one-dark";

/* Editor chrome through brand tokens; syntax colors come from the
 * one-dark highlight style (library palette, tuned for dark grounds). */
const brandTheme = EditorView.theme(
  {
    "&": {
      height: "100%",
      backgroundColor: "var(--ul-bg-inset)",
      color: "var(--ul-body)",
      fontSize: "var(--ul-text-sm)",
    },
    ".cm-content": {
      fontFamily: "var(--ul-font-mono)",
      caretColor: "var(--ul-accent)",
      padding: "var(--ul-space-3) 0",
    },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--ul-accent)" },
    "&.cm-focused": { outline: "none" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "var(--ul-accent-wash)",
    },
    ".cm-activeLine": { backgroundColor: "transparent" },
    ".cm-activeLineGutter": {
      backgroundColor: "transparent",
      color: "var(--ul-muted)",
    },
    ".cm-gutters": {
      backgroundColor: "var(--ul-bg-inset)",
      color: "var(--ul-faint)",
      border: "none",
      borderRight: "1px solid var(--ul-line)",
      fontFamily: "var(--ul-font-mono)",
    },
    ".cm-scroller": { lineHeight: "1.6" },
  },
  { dark: true },
);

interface Props {
  /** document identity — a change re-creates the editor state */
  docKey: string;
  initialText: string;
  onChange: (text: string) => void;
  onSave: () => void;
  readOnly?: boolean;
  /** "plain" drops the markdown mode. Read with docKey — a change of mode
   *  only takes effect when the document identity changes too. */
  language?: "markdown" | "plain";
}

export default function Editor({
  docKey,
  initialText,
  onChange,
  onSave,
  readOnly,
  language = "markdown",
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const callbacks = useRef({ onChange, onSave });
  callbacks.current = { onChange, onSave };
  const readOnlyComp = useRef(new Compartment());

  useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: initialText,
      extensions: [
        keymap.of([
          {
            key: "Mod-s",
            run: () => {
              callbacks.current.onSave();
              return true;
            },
          },
        ]),
        basicSetup,
        ...(language === "plain" ? [] : [markdown({ base: markdownLanguage })]),
        syntaxHighlighting(oneDarkHighlightStyle),
        brandTheme,
        EditorView.lineWrapping,
        readOnlyComp.current.of(EditorState.readOnly.of(!!readOnly)),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) callbacks.current.onChange(u.state.doc.toString());
        }),
      ],
    });
    const v = new EditorView({ state, parent: host.current });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // recreate only when the document identity changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey]);

  useEffect(() => {
    view.current?.dispatch({
      effects: readOnlyComp.current.reconfigure(EditorState.readOnly.of(!!readOnly)),
    });
  }, [readOnly]);

  // External text replacement (task toggle from preview, conflict reload).
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== initialText) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: initialText } });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialText]);

  return <div className="editor-host" ref={host} />;
}
