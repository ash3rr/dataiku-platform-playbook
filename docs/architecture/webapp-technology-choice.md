# Should this search UI be a Dataiku webapp?

A team asked whether to build a lookup application in Dataiku or outside it. Someone on the team had suggested a Java tool. No decision had been made when we last worked on it. This page records the options and what we established about licensing.

## The request

* Business users with no platform licences (no Dataiku seat, no Snowflake account).
* They search or filter by fields such as an asset identifier (a serial-number style key).
* The matching records live in Snowflake. Each record points to one or more images stored in an S3 bucket.
* The app shows the filtered records and renders the images. Images are the output. The app does not extract data from them.
* Phase 2 (a guess at the time, not a requirement): use the images to find similar cases across records, perhaps by tagging, by identifier, or by image similarity.

Stripped down, phase 1 is a form, a Snowflake query that returns S3 keys, and an image viewer. It is a small CRUD-style search app.

## Two questions that define the backend

1. How images reach the browser. Pre-signed S3 URLs generated server-side (recommended) or a public bucket (no).
2. How a record maps to its S3 object. Is the S3 key already stored as a column against the identifier in Snowflake, or derived from the identifier by naming convention? This was the open data point.

## Licensing, as we finally understood it

This went back and forth, so the corrections are recorded.

| Access method | Dataiku Reader licence for end users? | Snowflake licence for end users? |
|---|---|---|
| User logs into DSS and opens the webapp | Yes | No |
| Webapp published from DSS with a public URL, no DSS login | No | No |
| Streamlit in Snowflake | n/a | Yes, every viewer needs a Snowflake account |
| Standalone app (Spring Boot, Streamlit, React plus API) | n/a | No, the app uses one service account |

* DSS webapps can be published with a publicly exposed URL that does not require a DSS login, so viewers do not need Reader licences. We first thought this was not supported, which was wrong.
* API Deployer is not needed to expose a webapp. API Deployer and API nodes are for REST APIs and model endpoints, not webapps. We conflated the two at first.
* With a DSS webapp, Snowflake queries run server-side through DSS's Snowflake connection (one service account). End users never touch Snowflake, so they need no Snowflake licence. The service account and a running, network-reachable DSS instance are the only requirements.
* Streamlit in Snowflake (SiS) apps are not publicly exposable, so every viewer needs a Snowflake account. For an audience with no existing licences that is expensive and operationally heavy.

What remained unconfirmed: whether public webapp exposure depends on the Dataiku edition or contract. If it matters, ask Dataiku support in writing.

## Options

### Dataiku webapp (Dash, Bokeh, plain HTML/JS, or a Code Studio Streamlit webapp)

For:
* DSS is already running, and licensing is fine with a public URL.
* Snowflake access is already wired through a connection.
* Becomes more relevant if phase 2 turns into an image ML pipeline (image processing, model training, writing results back to Snowflake).

Against:
* DSS is built for pipelines and ML. It is a poor fit for transactional search screens, and you end up fighting the platform for simple UI needs.
* S3 image rendering takes extra work. Generating pre-signed URLs dynamically and showing them usually means writing JavaScript inside the webapp anyway.
* The app runs on the DSS instance and shares its resources. A public URL means anyone with the link can reach it. DSS must be reachable by the audience.
* Maintenance: future developers inherit a search app inside a data science platform, and its life is tied to the platform. We saw this tie in practice: after the 14.5.1 upgrade every Code Studio webapp needed its template rebuilt, and duplicate vanity URLs broke webapp routing instance-wide (see [webapps.md](../platform/webapps.md)).

### Streamlit in Snowflake

For:
* Data already in Snowflake, no connector setup, no separate backend.
* Access controlled through Snowflake roles.

Against:
* Every viewer needs a Snowflake account. This is the deciding weakness for this audience.
* SiS cannot call S3 directly by default. Either configure an External Access Integration for outbound calls, or register the bucket as an external stage (images stay in S3, Snowflake references them, and the app generates pre-signed URLs). Moving images into an internal stage is possible but more expensive and pointless for a large image library.

### Spring Boot (the Java suggestion)

For:
* JDBC to Snowflake is well supported.
* AWS SDK generates pre-signed S3 URLs natively.
* Thymeleaf or React front end, normal web hosting and authentication, and no licence dependency.
* A good fit if the team is Java-heavy.

Against:
* A separate application to host, secure and run.

### Other lightweight options mentioned

Standalone Streamlit (Python, fastest prototype), Retool (low-code internal tools), React or Next.js with a FastAPI backend, or an AWS-native Lambda plus S3 design. The team's main language should decide between these more than anything else.

## Ranking we ended with

1. Dataiku public webapp, if DSS is already running and the team accepts the platform tie: lowest friction.
2. Spring Boot or another standalone app: most control, no licence dependency.
3. Streamlit in Snowflake: least suitable because of per-viewer Snowflake accounts.

Advice on phase 2: do not over-engineer phase 1 for it. Build phase 1 light, but keep the S3 key structure and the image metadata in Snowflake clean enough to support tagging and similarity later. If the webapp is only a viewer on top of later ML work, build it outside Dataiku regardless. If it needs to trigger or interact with ML pipelines, keeping it in Dataiku makes more sense.

## Open

* Team language (Java, Python, JS) and therefore the final choice.
* Whether S3 keys are stored per identifier or derivable.
* Timeline between phase 1 and phase 2.
* Edition or contract constraints on public webapps.
