import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import batch, custody, playable, runs, session_store, settings, verify
from multidownloader.runner import Job, Runner
from multidownloader.runs import Run
from multidownloader.sources import yt_dlp_base


class StreamsTests(unittest.TestCase):
    def test_merged_single_and_playlist(self):
        merged = {'id': 'v1', 'format_id': '137+140', 'requested_formats': [{'format_id': '137'}, {'format_id': '140'}]}
        self.assertEqual([{'id': 'v1', 'merged': True, 'formats': ['137', '140']}], yt_dlp_base.streams_of(merged))
        self.assertEqual([{'id': 'v2', 'merged': False, 'formats': ['18']}],
                         yt_dlp_base.streams_of({'id': 'v2', 'format_id': '18'}))
        both = yt_dlp_base.streams_of({'entries': [merged, {'id': 'v2', 'format_id': '18'}]})
        self.assertEqual([True, False], [s['merged'] for s in both])
        self.assertEqual([], yt_dlp_base.streams_of(None))


class YtDlpOptionsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = yt_dlp_base.YtDlpHandler('YouTube')

    def opts(self, **options):
        return self.handler._build_opts(str(self.tmp / 'out'), options, None)

    def test_files_stay_as_served_by_default(self):
        opts = self.opts()
        self.assertEqual('never', opts['fixup'])
        self.assertNotIn('keepvideo', opts)
        # nothing is embedded into the media
        names = [pp.get('key') for pp in opts.get('postprocessors') or []]
        self.assertFalse({'FFmpegMetadata', 'EmbedThumbnail', 'FFmpegEmbedSubtitle'} & set(names))
        self.assertNotIn('embedthumbnail', opts)

    def test_repairs_when_asked_and_keeping_streams(self):
        self.assertNotIn('fixup', self.opts(original_streams=False))
        self.assertTrue(self.opts(keep_streams=True)['keepvideo'])

    def test_settings_reach_the_backend(self):
        prefs = dict(settings.defaults(), keep_streams=True)
        opts = settings.download_options(prefs, 'YouTube')
        self.assertEqual((True, True), (opts['original_streams'], opts['keep_streams']))
        self.assertTrue(settings.defaults()['original_streams'])
        args = batch.build_arg_parser().parse_args(['links.txt', '--repair-files', '--keep-streams'])
        self.assertEqual((True, True), (args.repair_files, args.keep_streams))


class MergedDownloader:
    def download(self, source, url, options, out_dir=None):
        (Path(out_dir) / 'youtube').mkdir()
        (Path(out_dir) / 'youtube' / '2005-04-24_v1.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42 video')
        return {'streams': [{'id': 'v1', 'merged': True, 'formats': ['137', '140']}]}


class RunRecordsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.anchors = self.tmp / 'anchors.log'
        run = Run(self.tmp / 'downloads', anchor_path=self.anchors)
        try:
            Runner(run, MergedDownloader(), delays={}, default_delay=(0, 0)).run(
                [Job('https://youtu.be/v1', 'YouTube')])
        finally:
            run.finish()
        self.run_dir = run.dir
        self.rel = 'youtube/2005-04-24_v1.mp4'

    def test_manifest_and_custody_say_streams_were_joined(self):
        row = runs.read_manifest(self.run_dir / 'manifest.jsonl')[0]
        self.assertEqual((True, ['137', '140']), (row['merged'], row['formats']))
        captured = [e for e in custody.read_chain(self.run_dir / 'custody.log').entries if e['event'] == 'captured']
        self.assertEqual((True, ['137', '140']), (captured[0]['detail']['merged'], captured[0]['detail']['formats']))

    def fake_ffmpeg(self, ok=True):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if ok:
                Path(cmd[-1]).write_bytes(Path(cmd[cmd.index('-i') + 1]).read_bytes() + b' remuxed')
            return mock.Mock(returncode=0 if ok else 1, stderr='' if ok else 'Invalid data found')
        return run, calls

    def test_playable_copy_is_recorded_and_the_original_kept(self):
        root = self.run_dir.parent.parent
        original = (root / self.rel).read_bytes()
        runner, calls = self.fake_ffmpeg()
        copy = playable.make_playable_copy(self.run_dir, self.rel, url='https://youtu.be/v1', ffmpeg='ffmpeg',
                                           anchor_path=self.anchors, runner=runner)
        self.assertEqual('2005-04-24_v1 (playable).mp4', copy.name)
        self.assertIn('-c', calls[0])
        self.assertEqual('copy', calls[0][calls[0].index('-c') + 1])  # streams copied, never re-encoded
        self.assertEqual(original, (root / self.rel).read_bytes())
        sums = verify.read_sums(self.run_dir / 'SHA256SUMS.txt')
        self.assertIn('youtube/2005-04-24_v1 (playable).mp4', sums)
        rows = runs.read_manifest(self.run_dir / 'manifest.jsonl')
        self.assertEqual(('derived', self.rel), (rows[-1]['status'], rows[-1]['derived_from']))
        events = [e['event'] for e in custody.read_chain(self.run_dir / 'custody.log').entries]
        self.assertEqual(['copied', 'hashed', 'anchored'], events[-3:])
        report = verify.verify(self.run_dir, check_custody=True, anchor_path=self.anchors)
        self.assertTrue(report.ok, report.lines())
        self.assertEqual(2, report.checked)

    def test_refusals(self):
        runner, _calls = self.fake_ffmpeg(ok=False)
        with self.assertRaisesRegex(playable.PlayableError, 'Invalid data found'):
            playable.make_playable_copy(self.run_dir, self.rel, ffmpeg='ffmpeg', runner=runner)
        root = self.run_dir.parent.parent
        self.assertEqual(['2005-04-24_v1.mp4'], sorted(p.name for p in (root / 'youtube').iterdir()))
        with self.assertRaisesRegex(playable.PlayableError, 'ffmpeg is not installed'), \
                mock.patch.object(playable, 'ffmpeg_path', return_value=None):
            playable.make_playable_copy(self.run_dir, self.rel)
        (root / self.rel).write_bytes(b'edited')
        with self.assertRaisesRegex(playable.PlayableError, 'has changed since it was saved'):
            playable.make_playable_copy(self.run_dir, self.rel, ffmpeg='ffmpeg', runner=self.fake_ffmpeg()[0])
        with self.assertRaisesRegex(playable.PlayableError, 'Only video and audio'):
            (root / 'note.txt').write_text('x')
            playable.make_playable_copy(self.run_dir, 'note.txt', ffmpeg='ffmpeg')

    def test_package_includes_the_copy(self):
        from multidownloader import package
        playable.make_playable_copy(self.run_dir, self.rel, ffmpeg='ffmpeg', runner=self.fake_ffmpeg()[0])
        out = self.tmp / 'out'
        out.mkdir()
        bag = package.export(self.run_dir, out).path
        self.assertTrue((bag / 'data' / 'youtube' / '2005-04-24_v1 (playable).mp4').is_file())
        self.assertTrue(verify.verify(bag, check_custody=True).ok)


class ManifestFieldsTests(unittest.TestCase):
    def test_job_result_has_the_fields(self):
        result = runs.JobResult(run_id='r', input_url='u', normalized_url='u', platform='youtube', backend='yt-dlp',
                                status='saved')
        self.assertIn('merged', result.to_dict())
        self.assertIn('formats', json.loads(json.dumps(result.to_dict())))


if __name__ == '__main__':
    unittest.main()
