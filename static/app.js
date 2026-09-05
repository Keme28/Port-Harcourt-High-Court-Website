function setOptions(select, values, selected, placeholder) {
  if (!select) return;
  const current = selected || select.dataset.current || select.value || "";
  select.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = placeholder || "Select";
  select.appendChild(blank);
  const seen = new Set();
  (values || []).forEach((value) => {
    if (!value || seen.has(value)) return;
    seen.add(value);
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    if (value === current) opt.selected = true;
    select.appendChild(opt);
  });
  if (current && !seen.has(current)) {
    const opt = document.createElement("option");
    opt.value = current;
    opt.textContent = current;
    opt.selected = true;
    select.appendChild(opt);
  }
}

function listFor(data, name) {
  if (!name) return [];
  const key = String(name).trim();
  const upper = key.toUpperCase().replace(/\s+/g, "_");
  return (
    (data.lists && (data.lists[key] || data.lists[upper])) ||
    (data.natures && (data.natures[key] || data.natures[upper])) ||
    (data.particulars && (data.particulars[key] || data.particulars[upper])) ||
    (data.details && (data.details[key] || data.details[upper])) ||
    []
  );
}

function applyLookups() {
  const dataEl = document.getElementById("lookup-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const typeSelect = document.querySelector("[name='CASE TYPE']");
  const natureSelect = document.querySelector("[name='NATURE OF CLAIM']");
  const particularsSelect = document.querySelector("[name='PARTICULARS']");
  const detailsSelect = document.querySelector("[name='DETAILS']");
  const statusSelect = document.querySelector("[name='STATUS']");

  const refresh = () => {
    const type = typeSelect ? typeSelect.value : "";
    setOptions(
      natureSelect,
      listFor(data, type),
      natureSelect ? natureSelect.dataset.current : "",
      type ? "Select nature" : "Select case type first"
    );

    const nature = natureSelect ? natureSelect.value : "";
    const particulars = listFor(data, nature).length ? listFor(data, nature) : listFor(data, type);
    setOptions(
      particularsSelect,
      particulars,
      particularsSelect ? particularsSelect.dataset.current : "",
      nature || type ? "Select particulars" : "Select nature first"
    );

    const particular = particularsSelect ? particularsSelect.value : "";
    const status = statusSelect ? statusSelect.value : "";
    let details = listFor(data, particular);
    if (!details.length) details = listFor(data, nature);
    if (!details.length && status === "CONCLUDED") details = listFor(data, "CONCLUDED");
    if (!details.length) details = listFor(data, type);
    setOptions(
      detailsSelect,
      details,
      detailsSelect ? detailsSelect.dataset.current : "",
      particular || nature || type ? "Select details" : "Select particulars first"
    );
  };

  const clearDownstream = (el) => {
    if (el === typeSelect && natureSelect) natureSelect.dataset.current = "";
    if ((el === typeSelect || el === natureSelect) && particularsSelect) particularsSelect.dataset.current = "";
    if (el !== detailsSelect && detailsSelect) detailsSelect.dataset.current = "";
  };

  [typeSelect, natureSelect, particularsSelect, statusSelect].forEach((el) => {
    if (!el) return;
    el.addEventListener("change", () => {
      clearDownstream(el);
      refresh();
    });
  });
  refresh();
}

function bindCounselFill() {
  document.querySelectorAll("[data-counsel]").forEach((select) => {
    select.addEventListener("change", () => {
      const option = select.selectedOptions[0];
      if (!option || !option.dataset.phone) return;
      const prefix = select.dataset.counsel;
      const phone = document.querySelector(`[name='${prefix === "claimant" ? "PHONE NUMBER" : "PHONE NUMBER2"}']`);
      const email = document.querySelector(`[name='${prefix === "claimant" ? "E-MAIL" : "E-MAIL2"}']`);
      if (phone && !phone.value) phone.value = option.dataset.phone.split(",")[0].trim();
      if (email && !email.value) email.value = (option.dataset.email || "").split(",")[0].trim();
      const nameInput = document.querySelector(`[name='${prefix === "claimant" ? "CLAIMANT COUNSEL" : "DEFENDANT COUNSEL"}']`);
      if (nameInput) nameInput.value = option.value;
    });
  });
}

function bindCasePrefill() {
  const select = document.getElementById("suit-picker");
  const preview = document.getElementById("case-preview");
  if (!select || !preview) return;
  const cases = JSON.parse(document.getElementById("case-data").textContent);
  const show = () => {
    const item = cases.find((c) => c["SUIT NUMBER"] === select.value);
    if (!item) {
      preview.innerHTML = "<p class='muted'>Choose a suit number to pull parties and counsel from the register.</p>";
      return;
    }
    preview.innerHTML = `
      <strong>${item["SUIT NUMBER"]}</strong>
      <div>${item.PARTIES || ""}</div>
      <div class="muted">${item["CASE TYPE"] || ""} · ${item["NATURE OF CLAIM"] || ""}</div>
      <div class="muted">${item["CLAIMANT COUNSEL"] || "No claimant counsel"} v ${item["DEFENDANT COUNSEL"] || "No defendant counsel"}</div>
    `;
  };
  select.addEventListener("change", show);
  show();
}

document.addEventListener("DOMContentLoaded", () => {
  applyLookups();
  bindCounselFill();
  bindCasePrefill();
});
