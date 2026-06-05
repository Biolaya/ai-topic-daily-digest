document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.classList.contains("delete-form")) {
    if (!window.confirm("确认删除这条配置吗？")) {
      event.preventDefault();
    }
  } else if (form.classList.contains("confirm-form")) {
    const message = form.dataset.confirm || "确认执行此操作吗？";
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  }
});
