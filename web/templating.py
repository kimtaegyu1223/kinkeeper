"""공용 Jinja2 템플릿 인스턴스.

라우트마다 따로 만들면 base.html의 네비게이션에서 기능 플래그를 참조하는 전역을
한 곳에서 등록할 수 없다. 여기서 한 번만 만들어 전 라우트가 공유한다.

`weight_feature_enabled`는 값이 아니라 호출형 전역으로 등록한다. 값으로 굳혀 두면
런타임/테스트에서 settings를 바꿔도 반영되지 않으므로, 렌더 시점에 settings를 다시
읽도록 한다(다이어트 기능 유지·기본 비활성 결정, 2026-07-11).
"""

from fastapi.templating import Jinja2Templates

from shared.config import settings

templates = Jinja2Templates(directory="web/templates")
templates.env.globals["weight_feature_enabled"] = lambda: settings.weight_feature_enabled
