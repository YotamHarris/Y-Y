require 'json'
require 'net/http'
require 'openssl'
require 'base64'
require 'uri'
require 'fileutils'

module YY
  class Connect
    def initialize(key_id:, issuer_id:, key_content:)
      @key_id, @issuer_id = key_id, issuer_id
      @key = OpenSSL::PKey.read(key_content)
    end
    def b64(value)
      Base64.urlsafe_encode64(value, padding: false)
    end
    def token
      now = Time.now.to_i
      unsigned = [b64({alg: 'ES256', kid: @key_id, typ: 'JWT'}.to_json),
                  b64({iss: @issuer_id, iat: now - 10, exp: now + 600, aud: 'appstoreconnect-v1'}.to_json)].join('.')
      # JWT ES256 requires raw r||s; OpenSSL returns an ASN.1 DER sequence.
      signature = OpenSSL::ASN1.decode(@key.sign('SHA256', unsigned)).value.map { |n| n.value.to_s(16).rjust(64, '0') }.join
      "#{unsigned}.#{b64([signature].pack('H*'))}"
    end
    def request(method, path, body = nil)
      uri = URI("https://api.appstoreconnect.apple.com/v1#{path}")
      klass = {'GET' => Net::HTTP::Get, 'POST' => Net::HTTP::Post, 'PATCH' => Net::HTTP::Patch}.fetch(method)
      request = klass.new(uri)
      request['Authorization'] = "Bearer #{token}"
      request['Content-Type'] = 'application/json'
      request.body = body.to_json if body
      response = Net::HTTP.start(uri.host, uri.port, use_ssl: true, open_timeout: 20, read_timeout: 60) { |http| http.request(request) }
      raise "App Store Connect #{method} failed (#{response.code}): #{response.body.to_s[0, 1000]}" unless response.is_a?(Net::HTTPSuccess)
      response.body.to_s.empty? ? {} : JSON.parse(response.body)
    end
    def query(path, params)
      request('GET', "#{path}?#{URI.encode_www_form(params)}").fetch('data')
    end
    def app(bundle_id)
      result = query('/apps', {'filter[bundleId]' => bundle_id}).first
      raise "Create #{bundle_id} in App Store Connect first" unless result
      result.fetch('id')
    end
    def build(app_id, number)
      query('/builds', {'filter[app]' => app_id, 'filter[version]' => number, 'limit' => 10}).first
    end
    def group(app_id, name)
      # Paginate rather than assuming the requested group is on the first page.
      path = "/betaGroups?#{URI.encode_www_form({'filter[app]' => app_id, 'limit' => 200})}"
      loop do
        response = request('GET', path)
        group = response.fetch('data').find { |g| g.dig('attributes', 'name') == name && g.dig('attributes', 'isInternalGroup') }
        return group.fetch('id') if group
        next_url = response.dig('links', 'next')
        break unless next_url
        parsed = URI(next_url)
        raise 'Unexpected App Store Connect pagination URL' unless parsed.host == 'api.appstoreconnect.apple.com'
        path = parsed.request_uri.delete_prefix('/v1')
      end
      raise "Create internal TestFlight group #{name.inspect} first"
    end
    def assign(build_id, group_id)
      request('POST', "/betaGroups/#{group_id}/relationships/builds", {data: [{type: 'builds', id: build_id}]})
    end
    def assigned?(build_id, group_id)
      # Apple supports reading builds from the group, not GET /builds/{id}/betaGroups.
      path = "/betaGroups/#{group_id}/builds?limit=200"
      loop do
        response = request('GET', path)
        return true if response.fetch('data').any? { |build| build['id'] == build_id }
        next_url = response.dig('links', 'next')
        return false unless next_url
        parsed = URI(next_url)
        raise 'Unexpected App Store Connect pagination URL' unless parsed.host == 'api.appstoreconnect.apple.com'
        path = parsed.request_uri.delete_prefix('/v1')
      end
    end
    def ready?(build_id)
      detail = request('GET', "/builds/#{build_id}/buildBetaDetail").fetch('data')
      detail.dig('attributes', 'internalBuildState') == 'IN_BETA_TESTING'
    end
    def notes(build_id, text)
      localizations = query('/betaBuildLocalizations', {'filter[build]' => build_id})
      english = localizations.find { |l| l.dig('attributes', 'locale') == 'en-US' }
      if english
        request('PATCH', "/betaBuildLocalizations/#{english['id']}", {data: {type: 'betaBuildLocalizations', id: english['id'], attributes: {whatsNew: text}}})
      else
        request('POST', '/betaBuildLocalizations', {data: {type: 'betaBuildLocalizations', attributes: {locale: 'en-US', whatsNew: text}, relationships: {build: {data: {type: 'builds', id: build_id}}}}})
      end
    end
  end

  class Distribution
    def initialize(client:, app_id:, build_number:, group_name:, output:, sleeper: ->(seconds) { sleep(seconds) }, clock: -> { Process.clock_gettime(Process::CLOCK_MONOTONIC) })
      @client, @app_id, @number, @group_name, @output = client, app_id, build_number, group_name, output
      @sleeper, @clock = sleeper, clock
    end
    def state(status, extra = {})
      FileUtils.mkdir_p(@output)
      File.write(File.join(@output, 'state.json'), {status: status, build_number: @number, app_id: @app_id}.merge(extra).to_json)
    end
    def existing
      @client.build(@app_id, @number)
    end
    def wait_processed(timeout: 3600)
      deadline = @clock.call + timeout
      loop do
        build = existing
        processing = build&.dig('attributes', 'processingState')
        raise "Apple rejected processing: #{processing}" if %w[FAILED INVALID].include?(processing)
        return build if processing == 'VALID'
        state('processing')
        raise 'Apple processing timed out; rerun failed jobs to reconcile this same build' if @clock.call >= deadline
        @sleeper.call(20)
      end
    end
    def distribute(notes)
      build = wait_processed
      id = build.fetch('id')
      group = @client.group(@app_id, @group_name)
      @client.notes(id, notes)
      @client.assign(id, group) unless @client.assigned?(id, group)
      state('processing', build_id: id, group_id: group)
    end
    def verify(timeout: 600)
      build = wait_processed
      id = build.fetch('id')
      group = @client.group(@app_id, @group_name)
      deadline = @clock.call + timeout
      until @client.assigned?(id, group) && @client.ready?(id)
        raise 'Build is processed but not yet ready for internal testers; rerun failed jobs' if @clock.call >= deadline
        @sleeper.call(20)
      end
      state('ready', build_id: id, group_id: group)
    end
  end
end
