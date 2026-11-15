// お問い合わせフォームのURL（Googleフォーム）。決まったらここだけ変える
(function(){
  const URL = "https://docs.google.com/forms/d/e/1FAIpQLSfJGYue_OCfewZlpfqDTVq5RHAk9O2ouRHzboLLlfFQfStcrg/viewform";
  if (!URL) return;
  document.querySelectorAll("#contact").forEach(a => {
    a.href = URL; a.target = "_blank"; a.rel = "noopener"; a.textContent = "お問い合わせフォーム";
  });
})();
