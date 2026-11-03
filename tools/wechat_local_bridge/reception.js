"use strict";

const get = (id) => document.getElementById(id);
const token = new URLSearchParams(location.search).get("local");
const result = get("result");
let current;
let settings;
let dirty = false;
let busy = false;
let groupIndex = -1;

async function api(path, body) {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"X-Bridge-Local-Key": token, "Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "本机桥接已退出，请重新打开工具");
  return payload;
}

function element(tag, label, className) {
  const node = document.createElement(tag);
  if (label) node.textContent = label;
  if (className) node.className = className;
  return node;
}

function button(label, action, disabled = false) {
  const node = element("button", label);
  node.type = "button";
  node.disabled = disabled;
  node.addEventListener("click", action);
  return node;
}

function checkbox(label, checked, change) {
  const node = element("label", "", "row");
  const input = element("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", () => {
    change(input.checked);
    dirty = true;
    render();
  });
  node.append(input, document.createTextNode(label));
  return node;
}

function title(contact) {
  return contact.remark || contact.nickname || contact.id;
}

function toggle(values, id, checked) {
  return checked ? [...new Set([...values, id])] : values.filter((value) => value !== id);
}

function selected(id) {
  return !settings.paused.includes(id) && (Object.hasOwn(settings.rooms, id) ||
    settings.individuals.includes(id) ||
    settings.groups.some((group) => group.enabled && group.members.includes(id)));
}

async function request(action, feedback = result) {
  try {
    await action();
  } catch (error) {
    feedback.textContent = error.message;
  }
}

function contactRow(contact, duplicate, label, checked, change) {
  const row = element("div", "", "item");
  const controls = element("div", "", "row");
  const name = element("div", "", "name");
  name.append(element("strong", title(contact)));
  if (contact.remark && contact.nickname && contact.remark !== contact.nickname) {
    name.append(element("small", contact.nickname));
  }
  const delivery = current.delivery[contact.id];
  if (duplicate) name.append(element("small", "有同名会话，回复将留给人工核对"));
  if (delivery) name.append(element("small", delivery.state));
  controls.append(name, checkbox(label, checked, change));
  row.append(controls);
  return row;
}

function render() {
  if (!settings) return;
  const query = get("search").value.trim().toLocaleLowerCase();
  const titles = new Map();
  const normalized = (contact) => title(contact).normalize("NFKC").replace(/\s+/g, "").toLocaleLowerCase();
  for (const contact of current.contacts) {
    const key = normalized(contact);
    titles.set(key, (titles.get(key) || 0) + 1);
  }
  const duplicate = (contact) => titles.get(normalized(contact)) > 1;
  const contacts = current.contacts.filter((contact) =>
    [contact.remark, contact.nickname].some((text) => text.toLocaleLowerCase().includes(query)));
  const group = settings.groups[groupIndex];
  const groupSelect = get("group");
  const options = [new Option("所有联系人", "-1"), ...settings.groups.map((item, index) => new Option(item.name, index))];
  groupSelect.replaceChildren(...options);
  groupSelect.value = String(groupIndex);
  const groupActions = get("group-actions");
  groupActions.replaceChildren();
  groupActions.hidden = !group;
  if (group) {
    groupActions.append(checkbox("接待此分组", group.enabled, (enabled) => { group.enabled = enabled; }),
      button("删除分组", () => {
        settings.groups.splice(groupIndex, 1);
        groupIndex = -1;
        dirty = true;
        render();
      }));
  }
  const people = contacts.filter((contact) => !contact.is_group).map((contact) => {
    const list = group ? group.members : settings.individuals;
    const row = contactRow(contact, duplicate(contact), group ? "加入分组" : "单独接待", list.includes(contact.id), (checked) => {
      if (group) group.members = toggle(list, contact.id, checked);
      else settings.individuals = toggle(list, contact.id, checked);
    });
    if (list.includes(contact.id) || settings.paused.includes(contact.id) ||
        settings.groups.some((item) => item.enabled && item.members.includes(contact.id))) {
      row.firstChild.append(checkbox("暂停此人", settings.paused.includes(contact.id), (checked) => {
        settings.paused = toggle(settings.paused, contact.id, checked);
      }));
    }
    return row;
  });
  get("people").replaceChildren(...(people.length ? people : [element("p", "没有符合条件的联系人", "muted")]));
  const rooms = contacts.filter((contact) => contact.is_group).map((contact) => {
    const enabled = Object.hasOwn(settings.rooms, contact.id);
    const row = contactRow(contact, duplicate(contact), "接待此群", enabled, (checked) => {
      if (checked) settings.rooms[contact.id] = [];
      else delete settings.rooms[contact.id];
    });
    if (enabled) {
      const keywords = element("input", "", "keywords");
      keywords.value = settings.rooms[contact.id].join("，");
      keywords.placeholder = "触发词选填，用逗号分隔";
      keywords.setAttribute("aria-label", title(contact) + "的自动回复关键词");
      keywords.addEventListener("input", () => {
        settings.rooms[contact.id] = keywords.value.split(/[，,]/).map((word) => word.trim()).filter(Boolean);
        dirty = true;
      });
      row.append(keywords);
    }
    return row;
  });
  get("rooms").replaceChildren(...(rooms.length ? rooms : [element("p", "没有符合条件的群聊", "muted")]));
  const suspended = current.suspended.filter(selected);
  get("suspended-section").hidden = !suspended.length;
  get("suspended").replaceChildren(...suspended.map((id) => {
    const contact = current.contacts.find((item) => item.id === id);
    const row = element("div", "", "row item");
    row.append(element("span", contact ? title(contact) : "已不可用的微信会话"),
      button("恢复接待", () => void save(settings.enabled, [id]), !contact || dirty));
    return row;
  }));
  get("enable").disabled = !current.ready || busy;
  get("save").disabled = !current.ready || busy;
  get("pause").disabled = !current.settings.enabled || busy;
}

async function refresh() {
  const next = await api("scope");
  const needsRender = !current || !dirty && JSON.stringify(current) !== JSON.stringify(next);
  current = next;
  get("status").textContent = current.error || (current.settings.enabled ? "接待已启用" : "接待尚未启用");
  get("window-status").textContent = current.window_checking ? "正在检查微信窗口…" : current.window_status;
  get("window-check").hidden = current.window_ready || current.window_checking;
  if (needsRender) {
    if (!dirty) settings = structuredClone(current.settings);
    render();
  }
}

async function save(enabled, resume = [], stored = false) {
  if (busy || !settings) return;
  busy = true;
  await request(async () => {
    await api("scope", {settings: {...(stored ? current.settings : settings), enabled}, resume});
    dirty = false;
    current = undefined;
    await refresh();
    result.textContent = enabled ? "已保存，接待已启用" : "已保存，接待已暂停";
  });
  busy = false;
  render();
}

get("search").addEventListener("input", render);
get("group").addEventListener("change", (event) => { groupIndex = Number(event.target.value); render(); });
get("new-group").addEventListener("click", () => {
  if (!settings) return;
  const name = get("group-name").value.trim();
  if (!name || settings.groups.some((group) => group.name === name)) {
    result.textContent = "请输入一个未使用的分组名称";
    return;
  }
  settings.groups.push({name, members: [], enabled: false});
  groupIndex = settings.groups.length - 1;
  get("group-name").value = "";
  dirty = true;
  render();
});
get("refresh").addEventListener("click", () => void request(async () => {
  await api("refresh", {});
  await refresh();
  render();
}));
get("enable").addEventListener("click", () => void save(true));
get("save").addEventListener("click", () => void save(current.settings.enabled));
get("pause").addEventListener("click", () => void save(false, [], true));
get("window-check").addEventListener("click", () => void request(async () => {
  await api("window-check", {});
  await refresh();
}, get("window-status")));

void request(refresh);
setInterval(() => { if (!busy) void request(refresh); }, 2500);
