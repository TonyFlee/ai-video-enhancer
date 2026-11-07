import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Dropzone from "@/components/Dropzone";

function selectFile(container: HTMLElement, file: File) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
}

describe("Dropzone", () => {
  it("reports the chosen file and clears the input so it can be selected again", () => {
    const onFileSelected = vi.fn();
    const { container } = render(
      <Dropzone file={null} onFileSelected={onFileSelected} />,
    );
    const file = new File(["video"], "clip.mp4", { type: "video/mp4" });

    selectFile(container, file);

    expect(onFileSelected).toHaveBeenCalledTimes(1);
    expect(onFileSelected).toHaveBeenCalledWith(file);
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input?.value).toBe("");
  });

  it("rejects unsupported extensions without notifying the parent", () => {
    const onFileSelected = vi.fn();
    const { container } = render(
      <Dropzone
        file={null}
        onFileSelected={onFileSelected}
        allowedExtensions={[".mp4", ".mov"]}
      />,
    );

    selectFile(container, new File(["text"], "notes.txt", { type: "text/plain" }));

    expect(onFileSelected).not.toHaveBeenCalled();
    expect(screen.getByText(/Unsupported format "\.txt"/)).toBeInTheDocument();
    expect(screen.getByText(/\.mp4, \.mov/)).toBeInTheDocument();
  });

  it("rejects files above the size limit", () => {
    const onFileSelected = vi.fn();
    const { container } = render(
      <Dropzone file={null} onFileSelected={onFileSelected} maxBytes={1024} />,
    );

    selectFile(container, new File([new Uint8Array(2048)], "big.mp4"));

    expect(onFileSelected).not.toHaveBeenCalled();
    expect(screen.getByText(/limit is 1.0 KB/)).toBeInTheDocument();
  });

  it("rejects empty files", () => {
    const onFileSelected = vi.fn();
    const { container } = render(
      <Dropzone file={null} onFileSelected={onFileSelected} />,
    );

    selectFile(container, new File([], "empty.mp4"));

    expect(onFileSelected).not.toHaveBeenCalled();
    expect(screen.getByText("That file is empty.")).toBeInTheDocument();
  });

  it("accepts dropped files and shows the selected file name", () => {
    const onFileSelected = vi.fn();
    const { rerender } = render(
      <Dropzone file={null} onFileSelected={onFileSelected} />,
    );
    const file = new File(["video"], "dropped.mov", { type: "video/quicktime" });

    fireEvent.drop(screen.getByRole("button", { name: "Upload a video" }), {
      dataTransfer: { files: [file] },
    });

    expect(onFileSelected).toHaveBeenCalledWith(file);

    rerender(<Dropzone file={file} onFileSelected={onFileSelected} />);
    expect(screen.getByText("dropped.mov")).toBeInTheDocument();
    expect(screen.getByText(/5 B/)).toBeInTheDocument();
  });

  it("shows the upload limit only when one is provided", () => {
    const { rerender } = render(
      <Dropzone file={null} onFileSelected={vi.fn()} />,
    );
    expect(screen.getByText("MP4, MOV, MKV, AVI, WEBM")).toBeInTheDocument();

    rerender(<Dropzone file={null} onFileSelected={vi.fn()} maxBytes={2048} />);
    expect(screen.getByText(/up to 2.0 KB/)).toBeInTheDocument();
  });
});
