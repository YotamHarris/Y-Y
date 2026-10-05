require 'minitest/autorun'
require 'tmpdir'
require_relative '../fastlane/lib/distribution'

class FakeConnect
  attr_accessor :processing, :assignment, :ready
  attr_reader :assignments
  def initialize
    @processing = 'PROCESSING'; @assignment = false; @ready = false; @assignments = 0
  end
  def build(*) = {'id' => 'build-1', 'attributes' => {'processingState' => @processing}}
  def group(*) = 'group-1'
  def notes(*) = nil
  def assigned?(*) = @assignment
  def assign(*)
    @assignments += 1; @assignment = true
  end
  def ready?(*) = @ready
end
class DistributionTest < Minitest::Test
  def setup
    @path = Dir.mktmpdir('yy-distribution')
    @client = FakeConnect.new
    @time = 0
    @dist = YY::Distribution.new(client: @client, app_id: 'app', build_number: '8', group_name: 'YY Internal', output: @path,
                                clock: -> { @time }, sleeper: ->(seconds) { @time += seconds })
  end
  def teardown = FileUtils.remove_entry(@path)
  def test_delayed_processing_never_reports_ready
    assert_raises(RuntimeError) { @dist.wait_processed(timeout: 20) }
    assert_equal 'processing', JSON.parse(File.read(File.join(@path, 'state.json')))['status']
  end
  def test_invalid_processing_fails
    @client.processing = 'INVALID'
    assert_raises(RuntimeError) { @dist.wait_processed }
  end
  def test_assignment_is_idempotent_and_ready_requires_apple_state
    @client.processing = 'VALID'
    @dist.distribute('notes'); @dist.distribute('notes')
    assert_equal 1, @client.assignments
    assert_raises(RuntimeError) { @dist.verify(timeout: 20) }
    @client.ready = true; @dist.verify
    assert_equal 'ready', JSON.parse(File.read(File.join(@path, 'state.json')))['status']
  end
  def test_apple_jwt_uses_raw_es256_signature_and_short_expiration
    key = OpenSSL::PKey::EC.generate('prime256v1')
    client = YY::Connect.new(key_id: 'test-key', issuer_id: 'test-issuer', key_content: key.private_to_pem)
    header, payload, signature = client.token.split('.')
    fields = JSON.parse(Base64.urlsafe_decode64(payload))
    assert_equal 'appstoreconnect-v1', fields['aud']
    assert_operator fields['exp'] - fields['iat'], :<=, 1200
    raw = Base64.urlsafe_decode64(signature)
    assert_equal 64, raw.bytesize
    integers = [raw[0, 32], raw[32, 32]].map { |part| OpenSSL::ASN1::Integer.new(OpenSSL::BN.new(part, 2)) }
    assert key.verify('SHA256', OpenSSL::ASN1::Sequence.new(integers).to_der, "#{header}.#{payload}")
  end
  def apple_client(responses)
    client = YY::Connect.allocate
    client.define_singleton_method(:request) do |method, path, body = nil|
      raise "Unexpected Apple request #{method} #{path}" unless method == 'GET' && responses.key?(path)
      responses.fetch(path)
    end
    client
  end
  def test_assignment_reads_group_builds_and_follows_pagination
    client = apple_client({
      '/betaGroups/group-1/builds?limit=200' => { 'data' => [{'id' => 'other-build'}],
        'links' => {'next' => 'https://api.appstoreconnect.apple.com/v1/betaGroups/group-1/builds?cursor=next'} },
      '/betaGroups/group-1/builds?cursor=next' => { 'data' => [{'id' => 'build-1'}], 'links' => {} }
    })
    assert client.assigned?('build-1', 'group-1')
    refute client.assigned?('missing-build', 'group-1')
  end
  def test_empty_group_is_not_assigned
    client = apple_client('/betaGroups/group-1/builds?limit=200' => { 'data' => [], 'links' => {} })
    refute client.assigned?('build-1', 'group-1')
  end
  def test_assignment_rejects_other_host_pagination
    client = apple_client('/betaGroups/group-1/builds?limit=200' => {
      'data' => [], 'links' => {'next' => 'https://example.com/v1/betaGroups/group-1/builds?cursor=next'} })
    assert_raises(RuntimeError) { client.assigned?('build-1', 'group-1') }
  end
end
