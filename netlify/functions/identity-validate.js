exports.handler = async (event) => {
  const body = JSON.parse(event.body);
  const email = body?.user?.email || "";

  const allowed = email.endsWith("@holidaypirates.com") || 
                  email.endsWith("@extern.holidaypirates.com");

  if (!allowed) {
    return {
      statusCode: 200,
      body: JSON.stringify({
        app_metadata: {},
        error: "Access restricted to HolidayPirates accounts."
      })
    };
  }

  return {
    statusCode: 200,
    body: JSON.stringify({ app_metadata: {} })
  };
};
