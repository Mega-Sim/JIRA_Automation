import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Seoul")

def norm_field_name(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


class TransitionWorkflow:
    def __init__(self, jira_client, log_fn, sccb_mode_getter):
        self.jira = jira_client
        self.log = log_fn
        self.get_mode = sccb_mode_getter  # "target" / "not_target"

    def _debug_print_transitions(self, issue_key, transitions):
        for t in transitions:
            to = t.get("to") or {}
            cat = (to.get("statusCategory") or {}).get("name", "")
            self.log(f"[DEBUG] {issue_key} transition name='{t.get('name')}', to='{to.get('name')}', category='{cat}'")

    def _find_transition_to_status(self, transitions, target_to_name: str):
        target = (target_to_name or "").strip().lower()
        for t in transitions:
            to_name = ((t.get("to") or {}).get("name") or "").strip().lower()
            if to_name == target:
                return t
        return None

    def _find_transition_to_done_category(self, transitions):
        for t in transitions:
            to = t.get("to") or {}
            cat = (to.get("statusCategory") or {}).get("name", "")
            if str(cat).strip().lower() == "done":
                return t
        return None

    def _find_transition_by_name(self, transitions, target_name: str):
        target = (target_name or "").strip().lower()
        for t in transitions:
            name = (t.get("name") or "").strip().lower()
            if name == target:
                return t
        return None

    # ---- 정책값 ----
    # Jira REST date 필드는 'YYYY-MM-DD'가 안전함(슬래시 형식은 400 parsing error 발생)
    def _policy_end_date_str(self) -> str:
        now = datetime.now(TZ)
        mode = (self.get_mode() or "").strip().lower()
        if mode == "target":
            monday = now.date() - timedelta(days=now.date().weekday())
            return monday.strftime("%Y-%m-%d")
        # not_target: 클릭한 날짜(오늘)
        return now.strftime("%Y-%m-%d")

    def _this_week_monday_slash(self) -> str:
        now = datetime.now(TZ)
        mode = (self.get_mode() or "").strip().lower()
        if mode == "target":
            monday = now.date() - timedelta(days=now.date().weekday())
            return monday.strftime("%Y-%m-%d")
        # not_target: 클릭한 날짜(오늘)
        return now.strftime("%Y-%m-%d")

    def _previous_week_wednesday_str(self) -> str:
        now = datetime.now(TZ)
        d = now.date()
        # 전주 수요일 = 이번 주 월요일 기준 5일 전
        prev_wed = d - timedelta(days=d.weekday() + 5)
        return prev_wed.strftime("%Y-%m-%d")

    def _voc_start_end_date_policy(self):
        """VOC 완료처리용 날짜 정책.

        End Date는 처리 클릭일(오늘)로 두고, Start Date는 End Date보다
        13/14/15일 빠른 날짜 중 하나를 VOC 이슈별로 랜덤 적용한다.
        Jira REST date 필드는 YYYY-MM-DD 형식으로 전달한다.
        """
        end_date = datetime.now(TZ).date()
        diff_days = random.choice((13, 14, 15))
        start_date = end_date - timedelta(days=diff_days)
        return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), diff_days

    def _is_end_date_field(self, fid: str, name: str) -> bool:
        n = norm_field_name(name)
        return (
            name == "End Date"
            or fid == "customfield_11903"
            or ("enddate" in n)
            or ("end" in n and "date" in n)
        )

    def _is_start_date_field(self, fid: str, name: str) -> bool:
        n = norm_field_name(name)
        return (
            name in ("Start Date", "Start date", "StartDate")
            or ("startdate" in n)
            or ("start" in n and "date" in n)
            or ("시작" in name and ("일" in name or "날짜" in name))
        )

    def _policy_sccb_value_label(self) -> str:
        return "SCCB 완료"

    def _policy_resolution_label(self) -> str:
        return "완료"

    def _pick_allowed_by_label(self, meta: dict, label: str):
        label = (label or "").strip()
        allowed = (meta or {}).get("allowedValues") or []
        for v in allowed:
            if isinstance(v, dict):
                nm = str(v.get("name") or "").strip()
                val = str(v.get("value") or "").strip()
                if nm == label or val == label:
                    if "id" in v:
                        return {"id": v["id"]}
                    if "name" in v:
                        return {"name": v["name"]}
                    if "value" in v:
                        return {"value": v["value"]}
                    return v
            else:
                if str(v).strip() == label:
                    return v
        return None

    def _build_transition_payload(self, transition_obj):
        payload = {"transition": {"id": transition_obj["id"]}}
        fields = transition_obj.get("fields") or {}

        required_fields = {fid: meta for fid, meta in fields.items() if (meta or {}).get("required")}
        if required_fields:
            payload["fields"] = {}

        def pick_first_allowed(meta):
            allowed = (meta or {}).get("allowedValues") or []
            if not allowed:
                return None
            v0 = allowed[0]
            if isinstance(v0, dict):
                if "id" in v0: return {"id": v0["id"]}
                if "value" in v0: return {"value": v0["value"]}
                if "name" in v0: return {"name": v0["name"]}
                return v0
            return v0

        def pick_default(meta):
            dv = (meta or {}).get("defaultValue")
            if dv is None:
                return None
            if isinstance(dv, dict):
                if "id" in dv: return {"id": dv["id"]}
                if "value" in dv: return {"value": dv["value"]}
                if "name" in dv: return {"name": dv["name"]}
                return dv
            return dv

        def fallback_by_schema(meta):
            schema = (meta or {}).get("schema") or {}
            t = (schema.get("type") or "").lower()
            now = datetime.now(TZ)
            if t == "date": return now.strftime("%Y-%m-%d")
            if t == "datetime": return now.isoformat(timespec="seconds")
            if t in ("number", "integer"): return 0
            if t == "array": return []
            return "AUTO"

        for fid, meta in required_fields.items():
            meta = meta or {}
            name = (meta.get("name") or "").strip()
            n = norm_field_name(name)

            if self._is_end_date_field(fid, name):
                payload["fields"][fid] = self._policy_end_date_str()
                continue

            if name == "SCCB 상태" or fid == "customfield_16107" or ("sccb" in n and ("상태" in name or "state" in n)):
                v = self._pick_allowed_by_label(meta, self._policy_sccb_value_label())
                payload["fields"][fid] = v if v is not None else {"value": self._policy_sccb_value_label()}
                continue

            if name == "해결책" or "해결책" in name or "해결책" in n or n == "resolution":
                v = self._pick_allowed_by_label(meta, self._policy_resolution_label())
                payload["fields"][fid] = v if v is not None else {"name": self._policy_resolution_label()}
                continue

            val = pick_first_allowed(meta) or pick_default(meta) or fallback_by_schema(meta)
            payload["fields"][fid] = val

        if "fields" in payload:
            payload["fields"] = {k: v for k, v in payload["fields"].items() if v is not None}
            if not payload["fields"]:
                payload.pop("fields")

        return payload

    def _do_transition(self, issue_key, transition_obj, extra_fields=None):
        payload = self._build_transition_payload(transition_obj)
        if extra_fields:
            payload.setdefault("fields", {})
            payload["fields"].update(extra_fields)
        self.jira.do_transition(issue_key, payload)

    def process_voc_linked_issues_from_parent(self, parent_issue_key: str):
        voc_keys = self.jira.get_linked_sw_voc_keys(parent_issue_key)
        if not voc_keys:
            self.log(f"{parent_issue_key}: 연결된 SW_VOC 이슈 없음 - 스킵")
            return

        self.log(f"{parent_issue_key}: 연결된 SW_VOC {', '.join(voc_keys)}")
        for voc_key in voc_keys:
            self._process_single_voc_issue_to_done(parent_issue_key, voc_key)

    def _process_single_voc_issue_to_done(self, parent_issue_key: str, voc_key: str):
        cur = self.jira.get_issue_status(voc_key)
        cur_l = (cur or "").strip().lower()
        if cur_l in ("완료", "done"):
            self.log(f"{parent_issue_key} -> {voc_key}: 이미 완료 상태")
            return

        trans = self.jira.get_transitions(voc_key)
        self._debug_print_transitions(voc_key, trans)

        t_done = (
            self._find_transition_to_status(trans, "완료")
            or self._find_transition_to_status(trans, "Done")
            or self._find_transition_by_name(trans, "done")
            or self._find_transition_to_done_category(trans)
        )
        if not t_done:
            self.log(f"{parent_issue_key} -> {voc_key}: Done 전이 없음 (현재={cur})")
            return

        voc_start_date, voc_end_date, voc_diff_days = self._voc_start_end_date_policy()

        pre_fields = {}
        fid_end = self.jira.find_field_id("End Date")
        if fid_end:
            pre_fields[fid_end] = voc_end_date
        else:
            pre_fields["customfield_11903"] = voc_end_date

        fid_start = self.jira.find_field_id("Start Date") or self.jira.find_field_id("Start date")
        if fid_start:
            pre_fields[fid_start] = voc_start_date

        if pre_fields:
            self.jira.update_issue_fields(voc_key, pre_fields)

        extra_fields = {}
        fields_meta = t_done.get("fields") or {}
        for fid, meta in fields_meta.items():
            name = (meta or {}).get("name") or ""
            n = norm_field_name(name)

            if self._is_end_date_field(fid, name):
                extra_fields[fid] = voc_end_date
                continue

            if self._is_start_date_field(fid, name):
                extra_fields[fid] = voc_start_date
                continue

            if name == "해결책" or "해결책" in name or "해결책" in n or n == "resolution":
                v = self._pick_allowed_by_label(meta or {}, self._policy_resolution_label())
                if v is None:
                    v = {"name": self._policy_resolution_label()}
                extra_fields[fid] = v
                continue

        self._do_transition(voc_key, t_done, extra_fields=extra_fields)
        self.log(
            f"{parent_issue_key} -> {voc_key}: Done 처리 완료 "
            f"(Start Date={voc_start_date}, End Date={voc_end_date}, 차이={voc_diff_days}일)"
        )

    def process_issue_to_approval(self, issue_key: str, cached_status: str | None = None):
        """선택 이슈를 'Approval' 상태로만 전이한다 (Complete까지 가지 않음)."""
        cur = (cached_status or "").strip() or self.jira.get_issue_status(issue_key)
        cur_l = cur.lower()

        if cur_l in ("approval", "approver"):
            self.log(f"{issue_key}: 이미 Approval 상태")
            return

        if cur_l != "in verification":
            self.log(f"{issue_key}: 스킵 (현재 상태={cur}, In Verification만 Approval 전이 가능)")
            return

        trans = self.jira.get_transitions(issue_key)
        self._debug_print_transitions(issue_key, trans)

        t_to_approval = (
            self._find_transition_to_status(trans, "Approval")
            or self._find_transition_to_status(trans, "Approver")
            or self._find_transition_by_name(trans, "approval")
            or self._find_transition_by_name(trans, "approver")
        )
        if not t_to_approval:
            self.log(f"{issue_key}: Approval/Approver 전이 없음 (현재={cur})")
            return

        self._do_transition(issue_key, t_to_approval)
        to_name = (t_to_approval.get("to") or {}).get("name") or "Approval"
        self.log(f"{issue_key}: {cur} -> {to_name}")

        # 반영 확인 (최대 6초)
        for _ in range(10):
            time.sleep(0.6)
            if self.jira.get_issue_status(issue_key).strip().lower() in ("approval", "approver"):
                self.log(f"{issue_key}: Approval 반영 확인 완료")
                return
        self.log(f"{issue_key}: Approval 반영 확인 실패 (Jira에서 직접 확인 필요)")

    def process_issue_to_complete(self, issue_key: str, cached_status: str | None = None):
        cur = (cached_status or "").strip() or self.jira.get_issue_status(issue_key)
        cur_l = cur.lower()

        if cur_l == "in verification":
            trans = self.jira.get_transitions(issue_key)
            self._debug_print_transitions(issue_key, trans)

            t_to_approval = self._find_transition_to_status(trans, "Approval") or self._find_transition_to_status(trans, "Approver")
            if not t_to_approval:
                self.log(f"{issue_key}: Approval/Approver 전이 없음 (현재=In Verification)")
                return

            self._do_transition(issue_key, t_to_approval)
            self.log(f"{issue_key}: In Verification -> {(t_to_approval.get('to') or {}).get('name') or 'Approval'}")

            ok = False
            for _ in range(10):
                time.sleep(0.6)
                if self.jira.get_issue_status(issue_key).strip().lower() in ("approval", "approver"):
                    ok = True
                    break
            if not ok:
                self.log(f"{issue_key}: Approval 반영 확인 실패")
                return

            cur_l = self.jira.get_issue_status(issue_key).strip().lower()

        if cur_l in ("approval", "approver"):
            trans = self.jira.get_transitions(issue_key)
            self._debug_print_transitions(issue_key, trans)

            t_to_complete = self._find_transition_to_status(trans, "Complete") or self._find_transition_to_done_category(trans)
            if not t_to_complete:
                self.log(f"{issue_key}: Complete(또는 Done 카테고리) 전이 없음 (현재=Approval)")
                return

            extra_fields = {}
            mode = (self.get_mode() or "").strip().lower()
            is_not_target = (mode == "not_target")

            # End Date (API는 YYYY-MM-DD)
            cur_end = self.jira.get_issue_field(issue_key, "customfield_11903")
            extra_fields["customfield_11903"] = cur_end if cur_end else self._policy_end_date_str()

            # SCCB 상태 (SCCB 완료)
            cur_sccb = self.jira.get_issue_field(issue_key, "customfield_16107")
            extra_fields["customfield_16107"] = cur_sccb if cur_sccb else {"value": self._policy_sccb_value_label()}

            # 해결책 (완료) - transition meta에서 필드 id 찾아 세팅
            fields_meta = (t_to_complete.get("fields") or {})
            for fid, meta in fields_meta.items():
                name = (meta or {}).get("name") or ""
                nm = str(name).strip()

                if nm == "SCCB 완료일":
                    if not is_not_target:
                        extra_fields[fid] = self._this_week_monday_slash()
                    continue

                if nm == "사전 SCCB 의뢰일":
                    if not is_not_target:
                        extra_fields[fid] = self._previous_week_wednesday_str()
                    continue

                if nm == "해결책":
                    v = self._pick_allowed_by_label(meta or {}, self._policy_resolution_label())
                    if v is None:
                        v = {"name": self._policy_resolution_label()}
                    extra_fields[fid] = v
                    continue

            # 날짜 필드는 Complete 전에 edit API로 먼저 저장한다.
            # SCCB 미대상(not_target)은 '사전 SCCB 의뢰일', 'SCCB 완료일'을 공란으로 비운다.
            pre_fields = {}
            fid_req = self.jira.find_field_id("사전 SCCB 의뢰일")
            if fid_req:
                pre_fields[fid_req] = None if is_not_target else self._previous_week_wednesday_str()

            fid_done = self.jira.find_field_id("SCCB 완료일")
            if fid_done:
                pre_fields[fid_done] = None if is_not_target else self._this_week_monday_slash()

            if pre_fields:
                self.jira.update_issue_fields(issue_key, pre_fields)

            self._do_transition(issue_key, t_to_complete, extra_fields=extra_fields)
            if is_not_target:
                self.log(f"{issue_key}: Approval -> Complete (SCCB 미대상: 사전 SCCB 의뢰일/SCCB 완료일 공란 처리)")
            else:
                self.log(f"{issue_key}: Approval -> Complete")
            return

        self.log(f"{issue_key}: 스킵 (현재 상태={cur})")
