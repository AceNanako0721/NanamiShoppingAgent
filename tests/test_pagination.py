from test_store import make_store, setup_target

from nanami_shopping.public import empty_candidate
from nanami_shopping.service import Service


def test_batch_result_pagination_does_not_hide_later_platforms(tmp_path):
    store = make_store(tmp_path / "state")
    task = setup_target(store)

    class Adapter:
        def __init__(self, platform):
            self.platform = platform

        def search(self, query):
            return dict(
                platform=self.platform,
                candidates=[
                    empty_candidate(
                        self.platform, "https://example.test/" + str(i), "SYNTHETIC " + str(i)
                    )
                    | {"source_evidence": {"synthetic": True}}
                    for i in range(20)
                ],
            )

    service = Service(store)
    service._adapter = Adapter
    result = service.search(task, "test", ["rakuten", "yahoo"])
    assert result["data"]["total_candidates"] == 40
    assert result["data"]["next_offset"] == 20
    resumed = service.get_task(task, offset=20)
    assert len(resumed["data"]["candidates"]) == 20
    assert {x["platform"] for x in resumed["data"]["candidates"]} == {"yahoo"}
