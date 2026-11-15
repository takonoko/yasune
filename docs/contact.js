// お問い合わせフォームのURL（Googleフォーム）。決まったらここだけ変える
(function(){
  const URL = "";
  if (!URL) return;
  document.querySelectorAll("#contact").forEach(a => {
    a.href = URL; a.target = "_blank"; a.rel = "noopener"; a.textContent = "お問い合わせフォーム";
  });
})();
