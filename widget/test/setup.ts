// jsdom has <dialog> but not its modal methods; give it the minimal behaviour the widget relies on.
const proto = HTMLDialogElement.prototype as HTMLDialogElement & { showModal?: () => void };
if (!proto.showModal) {
  proto.showModal = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  proto.close = function (this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
}

// jsdom has no object URLs.
if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:heed-test";
  URL.revokeObjectURL = () => {};
}
